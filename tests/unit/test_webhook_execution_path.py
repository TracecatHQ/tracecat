"""Defensive regression tests for the webhook → workflow execution path.

This is a high-risk path: webhook requests are the primary external entry point
for triggering workflows. Any breakage here silently drops events from external
systems (SIEMs, EDRs, ticketing, Slack, etc.).

These tests verify the invariants that must hold across refactors of
WorkflowExecutionsService and the webhook router.
"""

from __future__ import annotations

import datetime
import json
import uuid
from collections.abc import Iterator
from hashlib import sha256
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from temporalio.client import Client
from temporalio.common import TypedSearchAttributes, WorkflowIDReusePolicy

from tracecat.auth.types import Role
from tracecat.db.models import WorkflowDefinition
from tracecat.dsl.common import DSLInput, DSLRunArgs
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import (
    CollectionObject,
    ExternalObject,
    InlineObject,
    ObjectRef,
)
from tracecat.storage.utils import deserialize_object
from tracecat.webhooks.router import (
    _annotate_webhook_trace,
    _incoming_webhook,
    _wrapped_payload,
    incoming_webhook_wait,
)
from tracecat.webhooks.router import router as webhook_router
from tracecat.webhooks.schemas import WebhookRead
from tracecat.workflow.executions.enums import (
    ExecutionType,
    TemporalSearchAttr,
    TriggerType,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WF_ID = WorkflowUUID.new_uuid4()
_WORKSPACE_ID = uuid.uuid4()


def test_public_webhook_trace_annotation_sets_trace_id() -> None:
    response = cast(Any, {})
    with patch(
        "tracecat.webhooks.router.current_trace_id",
        return_value="0123456789abcdef0123456789abcdef",
    ):
        _annotate_webhook_trace(
            wf_id=_WF_ID,
            wf_exec_id="wf_synthetic/exec_synthetic",
            response=response,
        )

    assert response["trace_id"] == "0123456789abcdef0123456789abcdef"


def _role() -> Role:
    return Role(
        type="service",
        workspace_id=_WORKSPACE_ID,
        user_id=uuid.uuid4(),
        service_id="tracecat-runner",
    )


def _dsl_input(**overrides: Any) -> DSLInput:
    defaults = {
        "title": "Webhook regression test",
        "description": "test",
        "entrypoint": {"ref": "start"},
        "actions": [{"ref": "start", "action": "core.noop"}],
        "config": {"enable_runtime_tests": False},
    }
    defaults.update(overrides)
    return DSLInput(**defaults)


def _definition(**overrides: Any) -> WorkflowDefinition:
    content = {
        "title": "Webhook regression test",
        "description": "test",
        "entrypoint": {"ref": "start"},
        "actions": [{"ref": "start", "action": "core.noop"}],
        "config": {"enable_runtime_tests": False},
    }
    content.update(overrides)
    return cast(
        WorkflowDefinition,
        SimpleNamespace(content=content, registry_lock=None),
    )


# ---------------------------------------------------------------------------
# WorkflowExecutionsService._start_workflow invariants
# ---------------------------------------------------------------------------


@pytest.fixture
def service(mock_client: Client) -> WorkflowExecutionsService:
    return WorkflowExecutionsService(client=mock_client, role=_role())


@pytest.fixture
def mock_storage(service: WorkflowExecutionsService) -> Iterator[MagicMock]:
    storage = MagicMock()
    storage.store = AsyncMock(
        return_value=InlineObject(type="inline", data={"_": "ref"})
    )
    with (
        patch(
            "tracecat.workflow.executions.service.get_object_storage",
            return_value=storage,
        ),
        patch.object(service, "_resolve_execution_timeout", return_value=None),
    ):
        yield storage


class TestWebhookStartWorkflowInvariants:
    """Verify that _start_workflow preserves all webhook-critical invariants.

    These tests mock the Temporal client to inspect the exact arguments passed
    to start_workflow, ensuring nothing is silently dropped or mis-typed.
    """

    @pytest.fixture
    def mock_client(self) -> Client:
        client = MagicMock(spec=Client)
        client.start_workflow = AsyncMock(return_value=MagicMock())
        return client

    @pytest.mark.anyio
    @pytest.mark.parametrize("trigger_type", [TriggerType.WEBHOOK, TriggerType.FORM])
    async def test_start_workflow_webhook_invariants(
        self,
        service: WorkflowExecutionsService,
        mock_client: MagicMock,
        mock_storage: MagicMock,
        trigger_type: TriggerType,
    ):
        """Webhook-critical invariants for _start_workflow.

        - The trigger type and workspace ID are Temporal search attributes, so
          executions stay visible to webhook monitoring and tenant-scoped queries.
        - A time_anchor is minted when none is provided; webhooks have no
          TemporalScheduledStartTime fallback, so FN.now() would break.
        - The payload is persisted to object storage and passed as a StoredObject
          reference so large payloads don't exceed Temporal size limits.
        - The PUBLISHED execution type and registry lock are forwarded so child
          aliases and action versions resolve from committed definitions.
        """
        dsl = _dsl_input()
        payload = {"event_type": "alert.created", "data": {"id": "abc123"}}
        lock = RegistryLock(origins={"default": "v1.0"}, actions={})

        before = datetime.datetime.now(datetime.UTC)
        await service._start_workflow(
            dsl=dsl,
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_test",
            trigger_inputs=payload,
            trigger_type=trigger_type,
            execution_type=ExecutionType.PUBLISHED,
            registry_lock=lock,
            time_anchor=None,
        )
        after = datetime.datetime.now(datetime.UTC)

        mock_client.start_workflow.assert_awaited_once()
        call_args = mock_client.start_workflow.call_args
        search_attrs: TypedSearchAttributes = call_args.kwargs["search_attributes"]
        assert search_attrs.get(TemporalSearchAttr.TRIGGER_TYPE.key) == trigger_type
        assert search_attrs.get(TemporalSearchAttr.WORKSPACE_ID.key) == str(
            _WORKSPACE_ID
        )
        assert search_attrs.get(TemporalSearchAttr.EXECUTION_TYPE.key) == "published"

        mock_storage.store.assert_awaited_once()
        assert mock_storage.store.call_args.args[1] == payload

        dsl_run_args: DSLRunArgs = call_args.args[1]
        assert dsl_run_args.time_anchor is not None
        assert before <= dsl_run_args.time_anchor <= after
        assert dsl_run_args.trigger_inputs == mock_storage.store.return_value
        assert dsl_run_args.execution_type == ExecutionType.PUBLISHED
        assert dsl_run_args.registry_lock == lock

    @pytest.mark.anyio
    async def test_workflow_execution_ids_reject_duplicate_reuse(
        self,
        service: WorkflowExecutionsService,
        mock_client: MagicMock,
        mock_storage: MagicMock,
    ):
        await service._start_workflow(
            dsl=_dsl_input(),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_test",
            trigger_inputs={"x": 1},
            trigger_type=TriggerType.WEBHOOK,
        )

        call_kwargs = mock_client.start_workflow.call_args
        assert (
            call_kwargs.kwargs["id_reuse_policy"]
            == WorkflowIDReusePolicy.REJECT_DUPLICATE
        )

    @pytest.mark.anyio
    async def test_explicit_time_anchor_is_not_overwritten(
        self,
        service: WorkflowExecutionsService,
        mock_client: MagicMock,
        mock_storage: MagicMock,
    ):
        """If a time_anchor is explicitly provided it must not be replaced."""
        fixed_anchor = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
        await service._start_workflow(
            dsl=_dsl_input(),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_test",
            trigger_inputs={"x": 1},
            trigger_type=TriggerType.WEBHOOK,
            time_anchor=fixed_anchor,
        )

        call_args = mock_client.start_workflow.call_args
        dsl_run_args: DSLRunArgs = call_args.args[1]
        assert dsl_run_args.time_anchor == fixed_anchor


# ---------------------------------------------------------------------------
# Webhook router → service integration
# ---------------------------------------------------------------------------


class TestWebhookRouterExecutionPath:
    """Verify that the webhook router calls the service with correct arguments.

    These are higher-level tests that start from _incoming_webhook and verify
    the contract between the router and the execution service.
    """

    @staticmethod
    def _request(
        *,
        include_headers: bool = False,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> Request:
        request = MagicMock(spec=Request)
        request.headers = headers or {}
        request.body = AsyncMock(return_value=body)
        request.state.webhook_include_headers = include_headers
        return request

    @pytest.mark.anyio
    async def test_include_headers_on_preserves_legacy_envelope_expects(self):
        workflow_id = WorkflowUUID.new_uuid4()
        payload = {"event": "test"}
        mock_service = AsyncMock()
        mock_service.create_workflow_execution_wait_for_start = AsyncMock(
            return_value={
                "message": "Workflow execution started",
                "wf_id": workflow_id,
                "wf_exec_id": f"{workflow_id.short()}/exec_1",
            }
        )

        request = MagicMock(spec=Request)
        request.headers = {"content-type": "application/json"}
        request.body = AsyncMock(return_value=b'{"event": "test"}')

        defn = _definition(
            entrypoint={
                "ref": "start",
                "expects": {
                    "status_code": {"type": "int"},
                    "headers": {"type": "dict[str, str]"},
                    "data": {"type": "dict"},
                },
            }
        )

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=mock_service),
        ):
            await _incoming_webhook(
                workflow_id=workflow_id,
                defn=defn,
                payload=payload,
                echo=False,
                empty_echo=False,
                vendor=None,
                request=request,
                content_type="application/json",
                include_headers=True,
            )

        call_kwargs = (
            mock_service.create_workflow_execution_wait_for_start.call_args.kwargs
        )
        assert call_kwargs["payload"] == {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "data": payload,
        }

    @pytest.mark.anyio
    async def test_include_headers_on_keeps_raw_body_when_expects_declares_it(self):
        workflow_id = WorkflowUUID.new_uuid4()
        payload = {"event": "test"}
        mock_service = AsyncMock()
        mock_service.create_workflow_execution_wait_for_start = AsyncMock(
            return_value={
                "message": "Workflow execution started",
                "wf_id": workflow_id,
                "wf_exec_id": f"{workflow_id.short()}/exec_1",
            }
        )

        raw_body = b'{"event": "test"}'
        request = MagicMock(spec=Request)
        request.headers = {"content-type": "application/json"}
        request.body = AsyncMock(return_value=raw_body)

        defn = _definition(
            entrypoint={
                "ref": "start",
                "expects": {
                    "status_code": {"type": "int"},
                    "headers": {"type": "dict[str, str]"},
                    "data": {"type": "dict"},
                    "raw_body": {"type": "str"},
                },
            }
        )

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=mock_service),
        ):
            await _incoming_webhook(
                workflow_id=workflow_id,
                defn=defn,
                payload=payload,
                echo=False,
                empty_echo=False,
                vendor=None,
                request=request,
                content_type="application/json",
                include_headers=True,
            )

        call_kwargs = (
            mock_service.create_workflow_execution_wait_for_start.call_args.kwargs
        )
        assert call_kwargs["payload"] == {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "data": payload,
            "raw_body": raw_body.decode("utf-8"),
        }

    @pytest.mark.anyio
    async def test_include_headers_on_keeps_empty_raw_body_as_string(self):
        workflow_id = WorkflowUUID.new_uuid4()
        mock_service = AsyncMock()
        mock_service.create_workflow_execution_wait_for_start = AsyncMock(
            return_value={
                "message": "Workflow execution started",
                "wf_id": workflow_id,
                "wf_exec_id": f"{workflow_id.short()}/exec_1",
            }
        )

        request = MagicMock(spec=Request)
        request.headers = {"content-type": "application/json"}
        request.body = AsyncMock(return_value=b"")

        defn = _definition(
            entrypoint={
                "ref": "start",
                "expects": {
                    "status_code": {"type": "int"},
                    "headers": {"type": "dict[str, str]"},
                    "data": {"type": "Any"},
                    "raw_body": {"type": "str"},
                },
            }
        )

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=mock_service),
        ):
            await _incoming_webhook(
                workflow_id=workflow_id,
                defn=defn,
                payload=None,
                echo=False,
                empty_echo=False,
                vendor=None,
                request=request,
                content_type="application/json",
                include_headers=True,
            )

        call_kwargs = (
            mock_service.create_workflow_execution_wait_for_start.call_args.kwargs
        )
        assert call_kwargs["payload"] == {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "data": None,
            "raw_body": "",
        }

    @pytest.mark.anyio
    async def test_include_headers_on_wraps_each_ndjson_batch_item(self):
        """NDJSON batches: each execution gets its own wrapped envelope."""
        workflow_id = WorkflowUUID.new_uuid4()
        payload = [{"event": "a"}, {"event": "b"}]
        mock_service = AsyncMock()
        mock_service.create_workflow_execution_wait_for_start = AsyncMock(
            return_value={
                "message": "Workflow execution started",
                "wf_id": workflow_id,
                "wf_exec_id": f"{workflow_id.short()}/exec_1",
            }
        )

        request = MagicMock(spec=Request)
        request.headers = {"content-type": "application/x-ndjson"}
        request.body = AsyncMock(return_value=b'{"event":"a"}\n{"event":"b"}')

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=mock_service),
        ):
            await _incoming_webhook(
                workflow_id=workflow_id,
                defn=_definition(),
                payload=payload,
                echo=False,
                empty_echo=False,
                vendor=None,
                request=request,
                content_type="application/x-ndjson",
                include_headers=True,
            )

        # NDJSON batches of 8 -> both items land in a single batch list payload
        call_kwargs = (
            mock_service.create_workflow_execution_wait_for_start.call_args.kwargs
        )
        wrapped = call_kwargs["payload"]
        assert wrapped["status_code"] == 200
        assert wrapped["headers"] == {"content-type": "application/x-ndjson"}
        # batched() yields tuples; the whole batch is wrapped as one envelope
        assert list(wrapped["data"]) == payload

    @pytest.mark.anyio
    async def test_webhook_forwards_registry_lock_from_definition(self):
        """If the WorkflowDefinition has a registry_lock it must be forwarded."""
        workflow_id = WorkflowUUID.new_uuid4()
        lock_data = {"origins": {"default": "v2.0"}, "actions": {}}

        defn = cast(
            WorkflowDefinition,
            SimpleNamespace(
                content={
                    "title": "Test",
                    "description": "test",
                    "entrypoint": {"ref": "start"},
                    "actions": [{"ref": "start", "action": "core.noop"}],
                    "config": {"enable_runtime_tests": False},
                },
                registry_lock=lock_data,
            ),
        )

        mock_service = AsyncMock()
        mock_service.create_workflow_execution_wait_for_start = AsyncMock(
            return_value={
                "message": "Workflow execution started",
                "wf_id": workflow_id,
                "wf_exec_id": f"{workflow_id.short()}/exec_1",
            }
        )

        request = MagicMock(spec=Request)
        request.headers = {}

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=mock_service),
        ):
            await _incoming_webhook(
                workflow_id=workflow_id,
                defn=defn,
                payload={"x": 1},
                echo=False,
                empty_echo=False,
                vendor=None,
                request=request,
                content_type="application/json",
            )

        call_kwargs = (
            mock_service.create_workflow_execution_wait_for_start.call_args.kwargs
        )
        assert call_kwargs["trigger_type"] == TriggerType.WEBHOOK
        lock = call_kwargs["registry_lock"]
        assert isinstance(lock, RegistryLock)
        assert lock.origins == {"default": "v2.0"}

    @pytest.mark.anyio
    async def test_wait_webhook_returns_result_with_inline_object(self):
        """The /wait router path wraps inline results in a value envelope."""
        workflow_id = WorkflowUUID.new_uuid4()
        payload = {"event": "test"}
        inline_result = InlineObject(type="inline", data={"_": "result-ref"})
        mock_service = AsyncMock()
        mock_service.create_workflow_execution = AsyncMock(
            return_value={
                "wf_id": workflow_id,
                "result": inline_result,
            }
        )

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=mock_service),
        ):
            response = await incoming_webhook_wait(
                workflow_id=workflow_id,
                defn=_definition(),
                payload=payload,
                request=self._request(),
            )

        response_obj = cast(dict[str, Any], response)
        assert response_obj["kind"] == "value"
        assert response_obj["value"] == {"_": "result-ref"}
        mock_service.create_workflow_execution.assert_awaited_once()
        call_kwargs = mock_service.create_workflow_execution.call_args.kwargs
        assert call_kwargs["trigger_type"] == TriggerType.WEBHOOK
        assert call_kwargs["payload"] == payload
        assert call_kwargs["wf_exec_id"].startswith(f"{workflow_id.short()}/exec_")

    @pytest.mark.anyio
    async def test_wait_webhook_returns_download_url_for_external_object(self):
        workflow_id = WorkflowUUID.new_uuid4()
        payload = {"event": "test"}
        external_result = ExternalObject(
            type="external",
            ref=ObjectRef(
                backend="s3",
                bucket="tracecat-workflow",
                key="wf/test/return.json",
                size_bytes=42,
                sha256="abc123",
                content_type="application/json",
                encoding="json",
            ),
        )
        mock_service = AsyncMock()
        mock_service.create_workflow_execution = AsyncMock(
            return_value={
                "wf_id": workflow_id,
                "result": external_result,
            }
        )

        with (
            patch(
                "tracecat.webhooks.router.WorkflowExecutionsService.connect",
                AsyncMock(return_value=mock_service),
            ),
            patch(
                "tracecat.webhooks.router.blob.generate_presigned_download_url",
                AsyncMock(return_value="https://example.com/presigned/external"),
            ),
        ):
            response = await incoming_webhook_wait(
                workflow_id=workflow_id,
                defn=_definition(),
                payload=payload,
                request=self._request(),
            )

        response_obj = cast(dict[str, Any], response)
        assert response_obj["kind"] == "download_file"
        assert response_obj["download_url"] == "https://example.com/presigned/external"

    @pytest.mark.anyio
    async def test_wait_webhook_returns_single_download_url_for_collection_object(self):
        workflow_id = WorkflowUUID.new_uuid4()
        payload = {"event": "test"}
        collection_result = CollectionObject(
            type="collection",
            manifest_ref=ObjectRef(
                backend="s3",
                bucket="tracecat-workflow",
                key="wf/test/return/manifest.json",
                size_bytes=128,
                sha256="manifest-sha",
                content_type="application/json",
                encoding="json",
            ),
            count=3,
            chunk_size=2,
            element_kind="value",
        )
        mock_service = AsyncMock()
        mock_service.create_workflow_execution = AsyncMock(
            return_value={
                "wf_id": workflow_id,
                "result": collection_result,
            }
        )
        mock_storage = MagicMock()
        mock_storage.retrieve = AsyncMock(
            return_value=[{"id": 1}, {"id": 2}, {"id": 3}]
        )

        with (
            patch(
                "tracecat.webhooks.router.WorkflowExecutionsService.connect",
                AsyncMock(return_value=mock_service),
            ),
            patch(
                "tracecat.webhooks.router.get_collection_page",
                AsyncMock(return_value=[{"id": 1}, {"id": 2}, {"id": 3}]),
            ),
            patch(
                "tracecat.webhooks.router.blob.upload_file",
                AsyncMock(),
            ) as upload_file_mock,
            patch(
                "tracecat.webhooks.router.blob.generate_presigned_download_url",
                AsyncMock(return_value="https://example.com/presigned/collection"),
            ),
        ):
            response = await incoming_webhook_wait(
                workflow_id=workflow_id,
                defn=_definition(),
                payload=payload,
                request=self._request(),
            )

        response_obj = cast(dict[str, Any], response)
        assert response_obj["kind"] == "download_export"
        assert (
            response_obj["download_url"] == "https://example.com/presigned/collection"
        )
        upload_file_mock.assert_awaited_once()

    @pytest.mark.anyio
    async def test_wait_webhook_materializes_only_indexed_collection_item(self):
        workflow_id = WorkflowUUID.new_uuid4()
        payload = {"event": "test"}
        collection_result = CollectionObject(
            type="collection",
            manifest_ref=ObjectRef(
                backend="s3",
                bucket="tracecat-workflow",
                key="wf/test/return/manifest.json",
                size_bytes=128,
                sha256="manifest-sha",
                content_type="application/json",
                encoding="json",
            ),
            count=3,
            chunk_size=2,
            element_kind="value",
            index=1,
        )
        mock_service = AsyncMock()
        mock_service.create_workflow_execution = AsyncMock(
            return_value={
                "wf_id": workflow_id,
                "result": collection_result,
            }
        )
        get_page_mock = AsyncMock(return_value=[{"id": 2}])

        with (
            patch(
                "tracecat.webhooks.router.WorkflowExecutionsService.connect",
                AsyncMock(return_value=mock_service),
            ),
            patch(
                "tracecat.webhooks.router.get_collection_page",
                get_page_mock,
            ),
            patch(
                "tracecat.webhooks.router.blob.upload_file",
                AsyncMock(),
            ) as upload_file_mock,
            patch(
                "tracecat.webhooks.router.blob.generate_presigned_download_url",
                AsyncMock(return_value="https://example.com/presigned/collection"),
            ),
        ):
            response = await incoming_webhook_wait(
                workflow_id=workflow_id,
                defn=_definition(),
                payload=payload,
                request=self._request(),
            )

        response_obj = cast(dict[str, Any], response)
        assert response_obj["kind"] == "download_export"
        get_page_mock.assert_awaited_once_with(collection_result, offset=1, limit=1)

        await_args = upload_file_mock.await_args
        assert await_args is not None
        uploaded_content = await_args.kwargs["content"]
        assert deserialize_object(uploaded_content) == {"id": 2}

    @pytest.mark.anyio
    async def test_wait_webhook_materializes_collection_without_storage_backend(self):
        workflow_id = WorkflowUUID.new_uuid4()
        payload = {"event": "test"}
        external_payload = b'{"id":2}'
        external_ref = ExternalObject(
            type="external",
            ref=ObjectRef(
                backend="s3",
                bucket="tracecat-workflow",
                key="wf/test/chunks/0.json",
                size_bytes=len(external_payload),
                sha256=sha256(external_payload).hexdigest(),
                content_type="application/json",
                encoding="json",
            ),
        )
        collection_result = CollectionObject(
            type="collection",
            manifest_ref=ObjectRef(
                backend="s3",
                bucket="tracecat-workflow",
                key="wf/test/return/manifest.json",
                size_bytes=128,
                sha256="manifest-sha",
                content_type="application/json",
                encoding="json",
            ),
            count=2,
            chunk_size=2,
            element_kind="stored_object",
        )
        mock_service = AsyncMock()
        mock_service.create_workflow_execution = AsyncMock(
            return_value={
                "wf_id": workflow_id,
                "result": collection_result,
            }
        )

        with (
            patch(
                "tracecat.webhooks.router.WorkflowExecutionsService.connect",
                AsyncMock(return_value=mock_service),
            ),
            patch(
                "tracecat.webhooks.router.get_collection_page",
                AsyncMock(
                    return_value=[
                        InlineObject(type="inline", data={"id": 1}).model_dump(),
                        external_ref.model_dump(),
                    ]
                ),
            ),
            patch(
                "tracecat.webhooks.router.cached_blob_download",
                AsyncMock(return_value=external_payload),
            ) as cached_download_mock,
            patch(
                "tracecat.webhooks.router.blob.upload_file",
                AsyncMock(),
            ) as upload_file_mock,
            patch(
                "tracecat.webhooks.router.blob.generate_presigned_download_url",
                AsyncMock(return_value="https://example.com/presigned/collection"),
            ),
        ):
            response = await incoming_webhook_wait(
                workflow_id=workflow_id,
                defn=_definition(),
                payload=payload,
                request=self._request(),
            )

        response_obj = cast(dict[str, Any], response)
        assert response_obj["kind"] == "download_export"
        assert (
            response_obj["download_url"] == "https://example.com/presigned/collection"
        )
        cached_download_mock.assert_awaited_once()
        upload_file_mock.assert_awaited_once()

    @pytest.mark.anyio
    async def test_wait_webhook_unwrap_returns_raw_value_for_inline_object(self):
        """unwrap=True returns the workflow result directly for inline objects."""
        workflow_id = WorkflowUUID.new_uuid4()
        payload = {"event": "test"}
        inline_result = InlineObject(type="inline", data={"_": "result-ref"})
        mock_service = AsyncMock()
        mock_service.create_workflow_execution = AsyncMock(
            return_value={
                "wf_id": workflow_id,
                "result": inline_result,
            }
        )

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=mock_service),
        ):
            response = await incoming_webhook_wait(
                workflow_id=workflow_id,
                defn=_definition(),
                payload=payload,
                request=self._request(),
                unwrap=True,
            )

        assert isinstance(response, JSONResponse)
        assert isinstance(response.body, bytes)
        body = json.loads(response.body)
        assert body == {"_": "result-ref"}

    @pytest.mark.anyio
    async def test_wait_webhook_unwrap_returns_413_for_external_object(self):
        """unwrap=True raises 413 with the download envelope for externalized results."""
        workflow_id = WorkflowUUID.new_uuid4()
        payload = {"event": "test"}
        external_result = ExternalObject(
            type="external",
            ref=ObjectRef(
                backend="s3",
                bucket="tracecat-workflow",
                key="wf/test/return.json",
                size_bytes=42,
                sha256="abc123",
                content_type="application/json",
                encoding="json",
            ),
        )
        mock_service = AsyncMock()
        mock_service.create_workflow_execution = AsyncMock(
            return_value={
                "wf_id": workflow_id,
                "result": external_result,
            }
        )

        with (
            patch(
                "tracecat.webhooks.router.WorkflowExecutionsService.connect",
                AsyncMock(return_value=mock_service),
            ),
            patch(
                "tracecat.webhooks.router.blob.generate_presigned_download_url",
                AsyncMock(return_value="https://example.com/presigned/external"),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await incoming_webhook_wait(
                    workflow_id=workflow_id,
                    defn=_definition(),
                    payload=payload,
                    request=self._request(),
                    unwrap=True,
                )

        assert exc_info.value.status_code == 413
        detail = cast(dict[str, Any], exc_info.value.detail)
        assert detail["kind"] == "download_file"
        assert detail["download_url"] == "https://example.com/presigned/external"

    @pytest.mark.anyio
    async def test_wait_webhook_unwrap_returns_413_for_collection_object(self):
        """unwrap=True raises 413 with the download envelope for collection results."""
        workflow_id = WorkflowUUID.new_uuid4()
        payload = {"event": "test"}
        collection_result = CollectionObject(
            type="collection",
            manifest_ref=ObjectRef(
                backend="s3",
                bucket="tracecat-workflow",
                key="wf/test/return/manifest.json",
                size_bytes=128,
                sha256="manifest-sha",
                content_type="application/json",
                encoding="json",
            ),
            count=3,
            chunk_size=2,
            element_kind="value",
        )
        mock_service = AsyncMock()
        mock_service.create_workflow_execution = AsyncMock(
            return_value={
                "wf_id": workflow_id,
                "result": collection_result,
            }
        )

        with (
            patch(
                "tracecat.webhooks.router.WorkflowExecutionsService.connect",
                AsyncMock(return_value=mock_service),
            ),
            patch(
                "tracecat.webhooks.router.get_collection_page",
                AsyncMock(return_value=[{"id": 1}, {"id": 2}, {"id": 3}]),
            ),
            patch(
                "tracecat.webhooks.router.blob.upload_file",
                AsyncMock(),
            ),
            patch(
                "tracecat.webhooks.router.blob.generate_presigned_download_url",
                AsyncMock(return_value="https://example.com/presigned/collection"),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await incoming_webhook_wait(
                    workflow_id=workflow_id,
                    defn=_definition(),
                    payload=payload,
                    request=self._request(),
                    unwrap=True,
                )

        assert exc_info.value.status_code == 413
        detail = cast(dict[str, Any], exc_info.value.detail)
        assert detail["kind"] == "download_export"
        assert detail["download_url"] == "https://example.com/presigned/collection"

    def test_wait_webhook_openapi_keeps_default_200_schema_enveloped(self):
        """The default /wait 200 schema should stay discriminated for generated clients."""
        app = FastAPI()
        app.include_router(webhook_router)

        responses = app.openapi()["paths"]["/webhooks/{workflow_id}/{secret}/wait"][
            "post"
        ]["responses"]
        success_schema = responses["200"]["content"]["application/json"]["schema"]
        overflow_schema = responses["413"]["content"]["application/json"]["schema"]

        assert success_schema == {"$ref": "#/components/schemas/WaitResultOutput"}
        assert overflow_schema == {
            "$ref": "#/components/schemas/WaitResultUnwrapOverflowResponse"
        }


# ---------------------------------------------------------------------------
# _dispatch_workflow invariants (for /wait webhook endpoint)
# ---------------------------------------------------------------------------


class TestWebhookDispatchWorkflowInvariants:
    """Verify _dispatch_workflow (blocking execution) invariants for webhooks.

    The /wait endpoint uses create_workflow_execution which calls
    _dispatch_workflow. It has the same critical invariants as _start_workflow.
    """

    @pytest.fixture
    def mock_client(self) -> Client:
        client = MagicMock(spec=Client)
        # execute_workflow returns the workflow result
        client.execute_workflow = AsyncMock(return_value={"status": "ok"})
        return client

    @pytest.mark.anyio
    @pytest.mark.parametrize("trigger_type", [TriggerType.WEBHOOK, TriggerType.FORM])
    async def test_dispatch_mints_time_anchor_for_webhook(
        self,
        service: WorkflowExecutionsService,
        mock_client: MagicMock,
        mock_storage: MagicMock,
        trigger_type: TriggerType,
    ):
        """Same time_anchor, trigger-type and payload-storage invariants as _start_workflow."""
        before = datetime.datetime.now(datetime.UTC)
        await service._dispatch_workflow(
            dsl=_dsl_input(),
            wf_id=_WF_ID,
            wf_exec_id=f"{_WF_ID.short()}/exec_test",
            trigger_inputs={"x": 1},
            trigger_type=trigger_type,
        )
        after = datetime.datetime.now(datetime.UTC)

        call_args = mock_client.execute_workflow.call_args
        search_attrs: TypedSearchAttributes = call_args.kwargs["search_attributes"]
        assert search_attrs.get(TemporalSearchAttr.TRIGGER_TYPE.key) == trigger_type
        dsl_run_args: DSLRunArgs = call_args.args[1]
        assert dsl_run_args.time_anchor is not None
        assert before <= dsl_run_args.time_anchor <= after
        mock_storage.store.assert_awaited_once()
        assert dsl_run_args.trigger_inputs == mock_storage.store.return_value

    @pytest.mark.anyio
    async def test_dispatch_does_not_crash_for_inline_object_results(
        self, service: WorkflowExecutionsService, mock_client: MagicMock
    ):
        """Regression: /wait dispatch returns StoredObject results for inline values."""
        inline_result = InlineObject(type="inline", data={"_": "result-ref"})
        mock_client.execute_workflow.return_value = inline_result
        dsl = _dsl_input()

        with patch.object(service, "_resolve_execution_timeout", return_value=None):
            response = await service._dispatch_workflow(
                dsl=dsl,
                wf_id=_WF_ID,
                wf_exec_id=f"{_WF_ID.short()}/exec_test",
                trigger_inputs=None,
                trigger_type=TriggerType.WEBHOOK,
            )

        assert response["result"] == inline_result


# ---------------------------------------------------------------------------
# _wrapped_payload dependency (shared by /wait and /draft endpoints)
# ---------------------------------------------------------------------------


class TestWrappedPayloadDependency:
    """The /wait and /draft endpoints honor include_headers via _wrapped_payload.

    This is the same envelope the root POST/GET path produces, so TRIGGER has a
    consistent shape across all webhook trigger endpoints.
    """

    @staticmethod
    def _request(
        *,
        include_headers: bool,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> Request:
        request = MagicMock(spec=Request)
        request.headers = headers or {}
        request.body = AsyncMock(return_value=body)
        request.state.webhook_include_headers = include_headers
        return request

    @pytest.mark.anyio
    async def test_passthrough_when_flag_off(self):
        request = self._request(
            include_headers=False, headers={"content-type": "application/json"}
        )
        payload = {"alert": "x"}
        result = await _wrapped_payload(request=request, payload=payload)
        assert result == payload

    @pytest.mark.anyio
    async def test_wraps_when_flag_on(self):
        raw_body = b'{"alert": "x"}'
        request = self._request(
            include_headers=True,
            headers={"content-type": "application/json", "x-custom-source": "siem"},
            body=raw_body,
        )
        payload = {"alert": "x"}
        result = await _wrapped_payload(request=request, payload=payload)
        assert result == {
            "status_code": 200,
            "headers": {
                "content-type": "application/json",
                "x-custom-source": "siem",
            },
            "data": payload,
            "raw_body": raw_body.decode("utf-8"),
        }

    @pytest.mark.anyio
    async def test_omits_raw_body_when_not_schema_compatible(self):
        request = self._request(
            include_headers=True,
            headers={"content-type": "application/json", "x-custom-source": "siem"},
            body=b'{"alert": "x"}',
        )
        payload = {"alert": "x"}
        result = await _wrapped_payload(
            request=request, payload=payload, include_raw_body=False
        )
        assert result == {
            "status_code": 200,
            "headers": {
                "content-type": "application/json",
                "x-custom-source": "siem",
            },
            "data": payload,
        }

    @pytest.mark.anyio
    async def test_strips_api_key_when_flag_on(self):
        request = self._request(
            include_headers=True,
            headers={
                "content-type": "application/json",
                "x-tracecat-api-key": "super-secret",
            },
        )
        result = await _wrapped_payload(request=request, payload={"a": 1})
        assert result is not None
        assert "x-tracecat-api-key" not in result["headers"]
        assert result["headers"] == {"content-type": "application/json"}


class TestWebhookReadIncludeHeaders:
    """WebhookRead must tolerate a NULL include_headers on unflushed ORM objects."""

    def test_none_include_headers_coerced_to_false(self):
        obj = SimpleNamespace(
            id=uuid.uuid4(),
            secret="secret",
            status="online",
            entrypoint_ref=None,
            allowlisted_cidrs=None,
            filters=None,
            methods=None,
            include_headers=None,
            workflow_id=_WF_ID,
            url="http://localhost/webhooks/x/y",
            api_key=None,
        )
        read = WebhookRead.model_validate(obj, from_attributes=True)
        assert read.include_headers is False
