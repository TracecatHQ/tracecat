from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from botocore.exceptions import HTTPClientError
from temporalio.exceptions import ApplicationError

from tracecat.dsl import action
from tracecat.dsl.action import materialize_context
from tracecat.dsl.schemas import ExecutionContext, TaskResult
from tracecat.storage import blob as blob_module
from tracecat.storage.blob import get_storage_client
from tracecat.storage.object import CollectionObject, InlineObject, ObjectRef


def _close_run_sync_runner() -> None:
    runner = getattr(action._thread_local, "runner", None)
    if runner is not None:
        runner.close()
        delattr(action._thread_local, "runner")


def test_run_sync_reuses_storage_client_until_runner_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _close_run_sync_runner()
    blob_module.clear_storage_session_cache()
    monkeypatch.setattr(
        blob_module.config,
        "TRACECAT__BLOB_STORAGE_ENDPOINT",
        None,
        raising=False,
    )

    async def use_client() -> object:
        async with get_storage_client() as client:
            return client

    try:
        with patch("tracecat.storage.blob.aioboto3.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_client = AsyncMock()
            mock_session.client.return_value.__aenter__.return_value = mock_client

            assert action.run_sync(use_client()) is mock_client
            assert action.run_sync(use_client()) is mock_client

            mock_session_cls.assert_called_once()
            mock_session.client.assert_called_once_with(
                "s3", config=blob_module._STORAGE_CLIENT_CONFIG
            )
            mock_session.client.return_value.__aenter__.assert_awaited_once()
            mock_session.client.return_value.__aexit__.assert_not_awaited()

            _close_run_sync_runner()

            mock_session.client.return_value.__aexit__.assert_awaited_once_with(
                None, None, None
            )
            assert len(blob_module._STORAGE_CLIENTS) == 0
    finally:
        _close_run_sync_runner()
        blob_module.clear_storage_session_cache()


@pytest.mark.anyio
async def test_materialize_context_marks_storage_transport_errors_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raise_transport_error(*_args: object, **_kwargs: object) -> object:
        raise HTTPClientError(
            error=RuntimeError("File descriptor 512 is used by transport")
        )

    monkeypatch.setattr(action, "retrieve_stored_object", raise_transport_error)

    ctx = ExecutionContext(ACTIONS={}, TRIGGER=InlineObject(data={"trigger": 1}))

    with pytest.raises(ApplicationError) as exc_info:
        await materialize_context(ctx)

    assert exc_info.value.non_retryable is False
    assert exc_info.value.type == "StorageMaterializationError"


@pytest.mark.anyio
async def test_materialize_context_rehydrates_task_result_dicts() -> None:
    ctx = ExecutionContext(
        ACTIONS={
            "a1": TaskResult.from_result({"ok": True}),
        },
        TRIGGER=InlineObject(data={"trigger": 1}),
    )

    materialized = await materialize_context(ctx)

    # Verify keys exist (they're optional in TypedDict due to total=False)
    assert "ACTIONS" in materialized
    assert "TRIGGER" in materialized
    assert materialized["ACTIONS"]["a1"]["result"] == {"ok": True}
    assert materialized["ACTIONS"]["a1"]["result_typename"] == "dict"
    assert materialized["TRIGGER"] == {"trigger": 1}


@pytest.mark.anyio
async def test_materialize_task_result_collection_index_retrieves_single_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = CollectionObject(
        manifest_ref=ObjectRef(
            bucket="test-bucket",
            key="wf-123/manifest.json",
            size_bytes=256,
            sha256="abc123",
        ),
        count=10,
        chunk_size=5,
        element_kind="value",
    )
    task_result = TaskResult(
        result=collection,
        result_typename="dict",
        collection_index=3,
    )

    async def should_not_materialize_full_collection(*_args: object, **_kwargs: object):
        raise AssertionError("materialize_collection_values should not be called")

    class MockStorage:
        async def retrieve(self, stored: CollectionObject | InlineObject) -> object:
            assert isinstance(stored, CollectionObject)
            assert stored.index == 3
            return {"idx": 3}

    monkeypatch.setattr(
        action,
        "materialize_collection_values",
        should_not_materialize_full_collection,
    )
    monkeypatch.setattr(action, "get_object_storage", lambda: MockStorage())

    materialized = await action._materialize_task_result(task_result)

    assert materialized["result"] == {"idx": 3}


@pytest.mark.anyio
async def test_materialize_task_result_collection_index_preserves_list_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = CollectionObject(
        manifest_ref=ObjectRef(
            bucket="test-bucket",
            key="wf-123/manifest.json",
            size_bytes=256,
            sha256="abc123",
        ),
        count=2,
        chunk_size=2,
        element_kind="value",
    )
    task_result = TaskResult(
        result=collection,
        result_typename="list",
        collection_index=1,
    )

    class MockStorage:
        async def retrieve(self, stored: CollectionObject | InlineObject) -> object:
            assert isinstance(stored, CollectionObject)
            assert stored.index == 1
            # Simulate indexed retrieval for a list-valued element.
            return [3, 4]

    monkeypatch.setattr(action, "get_object_storage", lambda: MockStorage())

    materialized = await action._materialize_task_result(task_result)

    assert materialized["result"] == [3, 4]


@pytest.mark.anyio
async def test_materialize_task_result_inline_collection_index_returns_single_item() -> (
    None
):
    """Inline scatter results should honor collection_index during materialization."""
    task_result = TaskResult(
        result=InlineObject(
            data=[{"idx": 0, "name": "zero"}, {"idx": 1, "name": "one"}],
            typename="list",
        ),
        result_typename="dict",
        collection_index=1,
    )

    materialized = await action._materialize_task_result(task_result)

    assert materialized["result"] == {"idx": 1, "name": "one"}
