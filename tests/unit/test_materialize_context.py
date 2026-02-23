from __future__ import annotations

import pytest

from tracecat.dsl import action
from tracecat.dsl.action import materialize_context
from tracecat.dsl.schemas import ExecutionContext, TaskResult
from tracecat.storage.object import CollectionObject, InlineObject, ObjectRef


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
