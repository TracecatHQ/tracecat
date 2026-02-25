"""Defensive regression tests for the webhook → workflow execution path.

This is a high-risk path: webhook requests are the primary external entry point
for triggering workflows. Any breakage here silently drops events from external
systems (SIEMs, EDRs, ticketing, Slack, etc.).

These tests verify the invariants that must hold across refactors of
WorkflowExecutionsService and the webhook router.
"""

from __future__ import annotations

import datetime
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from temporalio.client import Client
from temporalio.common import TypedSearchAttributes

from tracecat.auth.types import Role
from tracecat.db.models import WorkflowDefinition
from tracecat.dsl.common import DSLInput, DSLRunArgs
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import InlineObject
from tracecat.webhooks.router import _incoming_webhook
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

    @pytest.fixture
    def service(self, mock_client: Client) -> WorkflowExecutionsService:
        role = _role()
        svc = WorkflowExecutionsService(client=mock_client, role=role)
        return svc

    @pytest.mark.anyio
    async def test_trigger_type_webhook_propagated_to_temporal_search_attrs(
        self, service: WorkflowExecutionsService, mock_client: MagicMock
    ):
        """TriggerType.WEBHOOK must appear in the Temporal search attributes.

        If the trigger type is lost or mapped to MANUAL, the execution will be
        invisible to webhook-specific monitoring and queries.
        """
        dsl = _dsl_input()
        payload = {"alert": "test"}
        mock_storage = MagicMock()
        mock_storage.store = AsyncMock(
            return_value=InlineObject(type="inline", data={"_": "ref"})
        )

        with (
            patch(
                "tracecat.workflow.executions.service.get_object_storage",
                return_value=mock_storage,
            ),
            patch.object(service, "_resolve_execution_timeout", return_value=None),
        ):
            await service._start_workflow(
                dsl=dsl,
                wf_id=_WF_ID,
                wf_exec_id=f"{_WF_ID.short()}/exec_test",
                trigger_inputs=payload,
                trigger_type=TriggerType.WEBHOOK,
            )

        mock_client.start_workflow.assert_awaited_once()
        call_kwargs = mock_client.start_workflow.call_args
        search_attrs: TypedSearchAttributes = call_kwargs.kwargs["search_attributes"]

        trigger_attr = TemporalSearchAttr.TRIGGER_TYPE.key
        found = [
            pair for pair in search_attrs.search_attributes if pair.key == trigger_attr
        ]
        assert found, (
            "TracecatTriggerType search attribute missing from start_workflow call"
        )
        assert found[0].value == "webhook", (
            f"TracecatTriggerType should be 'webhook', got '{found[0].value}'"
        )

    @pytest.mark.anyio
    async def test_time_anchor_is_minted_for_webhook_triggers(
        self, service: WorkflowExecutionsService, mock_client: MagicMock
    ):
        """Webhook triggers must mint a time_anchor when none is provided.

        The time_anchor drives FN.now() inside workflows. If omitted, scheduled
        workflows fall back to TemporalScheduledStartTime, but webhooks have no
        such fallback—they'd get a None time_anchor and FN.now() would break.
        """
        dsl = _dsl_input()
        mock_storage = MagicMock()
        mock_storage.store = AsyncMock(
            return_value=InlineObject(type="inline", data={"_": "ref"})
        )

        before = datetime.datetime.now(datetime.UTC)
        with (
            patch(
                "tracecat.workflow.executions.service.get_object_storage",
                return_value=mock_storage,
            ),
            patch.object(service, "_resolve_execution_timeout", return_value=None),
        ):
            await service._start_workflow(
                dsl=dsl,
                wf_id=_WF_ID,
                wf_exec_id=f"{_WF_ID.short()}/exec_test",
                trigger_inputs={"x": 1},
                trigger_type=TriggerType.WEBHOOK,
                time_anchor=None,  # <-- not provided
            )
        after = datetime.datetime.now(datetime.UTC)

        call_args = mock_client.start_workflow.call_args
        dsl_run_args: DSLRunArgs = call_args.args[1]
        assert dsl_run_args.time_anchor is not None, (
            "time_anchor must be minted for webhook triggers"
        )
        assert before <= dsl_run_args.time_anchor <= after

    @pytest.mark.anyio
    async def test_explicit_time_anchor_is_not_overwritten(
        self, service: WorkflowExecutionsService, mock_client: MagicMock
    ):
        """If a time_anchor is explicitly provided it must not be replaced."""
        dsl = _dsl_input()
        fixed_anchor = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
        mock_storage = MagicMock()
        mock_storage.store = AsyncMock(
            return_value=InlineObject(type="inline", data={"_": "ref"})
        )

        with (
            patch(
                "tracecat.workflow.executions.service.get_object_storage",
                return_value=mock_storage,
            ),
            patch.object(service, "_resolve_execution_timeout", return_value=None),
        ):
            await service._start_workflow(
                dsl=dsl,
                wf_id=_WF_ID,
                wf_exec_id=f"{_WF_ID.short()}/exec_test",
                trigger_inputs={"x": 1},
                trigger_type=TriggerType.WEBHOOK,
                time_anchor=fixed_anchor,
            )

        call_args = mock_client.start_workflow.call_args
        dsl_run_args: DSLRunArgs = call_args.args[1]
        assert dsl_run_args.time_anchor == fixed_anchor

    @pytest.mark.anyio
    async def test_trigger_inputs_stored_in_object_storage(
        self, service: WorkflowExecutionsService, mock_client: MagicMock
    ):
        """Webhook payload must be persisted to object storage and passed as StoredObject.

        If the payload is passed inline instead of as a StoredObject reference,
        large payloads will exceed Temporal's payload size limits and the
        execution will fail.
        """
        dsl = _dsl_input()
        payload = {"event_type": "alert.created", "data": {"id": "abc123"}}
        mock_stored = InlineObject(type="inline", data={"_": "ref"})
        mock_storage = MagicMock()
        mock_storage.store = AsyncMock(return_value=mock_stored)

        with (
            patch(
                "tracecat.workflow.executions.service.get_object_storage",
                return_value=mock_storage,
            ),
            patch.object(service, "_resolve_execution_timeout", return_value=None),
        ):
            await service._start_workflow(
                dsl=dsl,
                wf_id=_WF_ID,
                wf_exec_id=f"{_WF_ID.short()}/exec_test",
                trigger_inputs=payload,
                trigger_type=TriggerType.WEBHOOK,
            )

        # Object storage was called with the payload
        mock_storage.store.assert_awaited_once()
        store_args = mock_storage.store.call_args
        assert store_args.args[1] == payload

        # The DSLRunArgs received the StoredObject reference, not the raw dict
        call_args = mock_client.start_workflow.call_args
        dsl_run_args: DSLRunArgs = call_args.args[1]
        assert dsl_run_args.trigger_inputs == mock_stored

    @pytest.mark.anyio
    async def test_none_payload_skips_storage(
        self, service: WorkflowExecutionsService, mock_client: MagicMock
    ):
        """A None payload (e.g. empty-body webhook) must not call object storage."""
        dsl = _dsl_input()
        mock_storage = MagicMock()
        mock_storage.store = AsyncMock()

        with (
            patch(
                "tracecat.workflow.executions.service.get_object_storage",
                return_value=mock_storage,
            ),
            patch.object(service, "_resolve_execution_timeout", return_value=None),
        ):
            await service._start_workflow(
                dsl=dsl,
                wf_id=_WF_ID,
                wf_exec_id=f"{_WF_ID.short()}/exec_test",
                trigger_inputs=None,
                trigger_type=TriggerType.WEBHOOK,
            )

        mock_storage.store.assert_not_awaited()

        call_args = mock_client.start_workflow.call_args
        dsl_run_args: DSLRunArgs = call_args.args[1]
        assert dsl_run_args.trigger_inputs is None

    @pytest.mark.anyio
    async def test_execution_type_is_published_for_standard_webhook(
        self, service: WorkflowExecutionsService, mock_client: MagicMock
    ):
        """Standard webhook execution must use PUBLISHED execution type.

        If this changes to DRAFT, the workflow would resolve child-workflow
        aliases from draft definitions instead of committed ones, potentially
        running untested workflow versions in production.
        """
        dsl = _dsl_input()
        mock_storage = MagicMock()
        mock_storage.store = AsyncMock(
            return_value=InlineObject(type="inline", data={"_": "ref"})
        )

        with (
            patch(
                "tracecat.workflow.executions.service.get_object_storage",
                return_value=mock_storage,
            ),
            patch.object(service, "_resolve_execution_timeout", return_value=None),
        ):
            await service._start_workflow(
                dsl=dsl,
                wf_id=_WF_ID,
                wf_exec_id=f"{_WF_ID.short()}/exec_test",
                trigger_inputs={"x": 1},
                trigger_type=TriggerType.WEBHOOK,
                execution_type=ExecutionType.PUBLISHED,
            )

        call_args = mock_client.start_workflow.call_args
        dsl_run_args: DSLRunArgs = call_args.args[1]
        assert dsl_run_args.execution_type == ExecutionType.PUBLISHED

        # Also verify it's in the search attributes
        search_attrs: TypedSearchAttributes = call_args.kwargs["search_attributes"]
        exec_type_key = TemporalSearchAttr.EXECUTION_TYPE.key
        found = [
            pair for pair in search_attrs.search_attributes if pair.key == exec_type_key
        ]
        assert found and found[0].value == "published"

    @pytest.mark.anyio
    async def test_workspace_id_propagated_in_search_attrs(
        self, service: WorkflowExecutionsService, mock_client: MagicMock
    ):
        """Workspace ID must be set as a Temporal search attribute.

        Without it, multi-tenant query isolation breaks—workspace A could
        see (or be confused by) workspace B's executions.
        """
        dsl = _dsl_input()
        mock_storage = MagicMock()
        mock_storage.store = AsyncMock(
            return_value=InlineObject(type="inline", data={"_": "ref"})
        )

        with (
            patch(
                "tracecat.workflow.executions.service.get_object_storage",
                return_value=mock_storage,
            ),
            patch.object(service, "_resolve_execution_timeout", return_value=None),
        ):
            await service._start_workflow(
                dsl=dsl,
                wf_id=_WF_ID,
                wf_exec_id=f"{_WF_ID.short()}/exec_test",
                trigger_inputs={"x": 1},
                trigger_type=TriggerType.WEBHOOK,
            )

        call_args = mock_client.start_workflow.call_args
        search_attrs: TypedSearchAttributes = call_args.kwargs["search_attributes"]
        ws_key = TemporalSearchAttr.WORKSPACE_ID.key
        found = [pair for pair in search_attrs.search_attributes if pair.key == ws_key]
        assert found, "TracecatWorkspaceId search attribute missing"
        assert found[0].value == str(_WORKSPACE_ID)

    @pytest.mark.anyio
    async def test_registry_lock_forwarded_to_dsl_run_args(
        self, service: WorkflowExecutionsService, mock_client: MagicMock
    ):
        """Registry lock must be forwarded so that the worker pins exact action versions.

        If dropped, the worker resolves actions from the latest registry—which
        may differ from the versions the workflow was tested against.
        """
        dsl = _dsl_input()
        lock = RegistryLock(origins={"default": "v1.0"}, actions={})
        mock_storage = MagicMock()
        mock_storage.store = AsyncMock(
            return_value=InlineObject(type="inline", data={"_": "ref"})
        )

        with (
            patch(
                "tracecat.workflow.executions.service.get_object_storage",
                return_value=mock_storage,
            ),
            patch.object(service, "_resolve_execution_timeout", return_value=None),
        ):
            await service._start_workflow(
                dsl=dsl,
                wf_id=_WF_ID,
                wf_exec_id=f"{_WF_ID.short()}/exec_test",
                trigger_inputs={"x": 1},
                trigger_type=TriggerType.WEBHOOK,
                registry_lock=lock,
            )

        call_args = mock_client.start_workflow.call_args
        dsl_run_args: DSLRunArgs = call_args.args[1]
        assert dsl_run_args.registry_lock == lock


# ---------------------------------------------------------------------------
# Webhook router → service integration
# ---------------------------------------------------------------------------


class TestWebhookRouterExecutionPath:
    """Verify that the webhook router calls the service with correct arguments.

    These are higher-level tests that start from _incoming_webhook and verify
    the contract between the router and the execution service.
    """

    @pytest.mark.anyio
    async def test_standard_webhook_calls_wait_for_start_with_webhook_trigger(self):
        """The primary webhook handler must use create_workflow_execution_wait_for_start
        with trigger_type=WEBHOOK—never nowait or manual.
        """
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
        request.headers = {}

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
                content_type="application/json",
            )

        mock_service.create_workflow_execution_wait_for_start.assert_awaited_once()
        call_kwargs = (
            mock_service.create_workflow_execution_wait_for_start.call_args.kwargs
        )
        assert call_kwargs["trigger_type"] == TriggerType.WEBHOOK
        assert call_kwargs["payload"] == payload

    @pytest.mark.anyio
    async def test_webhook_constructs_dsl_input_from_definition_content(self):
        """The router must build DSLInput from defn.content, not pass it raw."""
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
        request.headers = {}

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=mock_service),
        ):
            await _incoming_webhook(
                workflow_id=workflow_id,
                defn=_definition(),
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
        dsl = call_kwargs["dsl"]
        assert isinstance(dsl, DSLInput)
        assert dsl.title == "Webhook regression test"

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
        lock = call_kwargs["registry_lock"]
        assert isinstance(lock, RegistryLock)
        assert lock.origins == {"default": "v2.0"}

    @pytest.mark.anyio
    async def test_webhook_response_contains_required_fields(self):
        """Response must include wf_id and wf_exec_id for caller correlation."""
        workflow_id = WorkflowUUID.new_uuid4()
        expected_exec_id = f"{workflow_id.short()}/exec_42"
        mock_service = AsyncMock()
        mock_service.create_workflow_execution_wait_for_start = AsyncMock(
            return_value={
                "message": "Workflow execution started",
                "wf_id": workflow_id,
                "wf_exec_id": expected_exec_id,
            }
        )

        request = MagicMock(spec=Request)
        request.headers = {}

        with patch(
            "tracecat.webhooks.router.WorkflowExecutionsService.connect",
            AsyncMock(return_value=mock_service),
        ):
            response = await _incoming_webhook(
                workflow_id=workflow_id,
                defn=_definition(),
                payload={"x": 1},
                echo=False,
                empty_echo=False,
                vendor=None,
                request=request,
                content_type="application/json",
            )

        assert isinstance(response, dict)
        assert "wf_id" in response
        assert "wf_exec_id" in response
        assert response["wf_exec_id"] == expected_exec_id


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

    @pytest.fixture
    def service(self, mock_client: Client) -> WorkflowExecutionsService:
        return WorkflowExecutionsService(client=mock_client, role=_role())

    @pytest.mark.anyio
    async def test_dispatch_mints_time_anchor_for_webhook(
        self, service: WorkflowExecutionsService, mock_client: MagicMock
    ):
        """Same time_anchor invariant as _start_workflow."""
        dsl = _dsl_input()
        mock_storage = MagicMock()
        mock_storage.store = AsyncMock(
            return_value=InlineObject(type="inline", data={"_": "ref"})
        )

        before = datetime.datetime.now(datetime.UTC)
        with (
            patch(
                "tracecat.workflow.executions.service.get_object_storage",
                return_value=mock_storage,
            ),
            patch.object(service, "_resolve_execution_timeout", return_value=None),
        ):
            await service._dispatch_workflow(
                dsl=dsl,
                wf_id=_WF_ID,
                wf_exec_id=f"{_WF_ID.short()}/exec_test",
                trigger_inputs={"x": 1},
                trigger_type=TriggerType.WEBHOOK,
            )
        after = datetime.datetime.now(datetime.UTC)

        call_args = mock_client.execute_workflow.call_args
        dsl_run_args: DSLRunArgs = call_args.args[1]
        assert dsl_run_args.time_anchor is not None
        assert before <= dsl_run_args.time_anchor <= after

    @pytest.mark.anyio
    async def test_dispatch_stores_payload_and_passes_stored_ref(
        self, service: WorkflowExecutionsService, mock_client: MagicMock
    ):
        """Same payload-storage invariant as _start_workflow."""
        dsl = _dsl_input()
        payload = {"alert_id": "A-001"}
        mock_stored = InlineObject(type="inline", data={"_": "ref"})
        mock_storage = MagicMock()
        mock_storage.store = AsyncMock(return_value=mock_stored)

        with (
            patch(
                "tracecat.workflow.executions.service.get_object_storage",
                return_value=mock_storage,
            ),
            patch.object(service, "_resolve_execution_timeout", return_value=None),
        ):
            await service._dispatch_workflow(
                dsl=dsl,
                wf_id=_WF_ID,
                wf_exec_id=f"{_WF_ID.short()}/exec_test",
                trigger_inputs=payload,
                trigger_type=TriggerType.WEBHOOK,
            )

        mock_storage.store.assert_awaited_once()
        call_args = mock_client.execute_workflow.call_args
        dsl_run_args: DSLRunArgs = call_args.args[1]
        assert dsl_run_args.trigger_inputs == mock_stored

    @pytest.mark.anyio
    async def test_dispatch_propagates_trigger_type_in_search_attrs(
        self, service: WorkflowExecutionsService, mock_client: MagicMock
    ):
        """Same trigger-type search-attr invariant as _start_workflow."""
        dsl = _dsl_input()
        mock_storage = MagicMock()
        mock_storage.store = AsyncMock(
            return_value=InlineObject(type="inline", data={"_": "ref"})
        )

        with (
            patch(
                "tracecat.workflow.executions.service.get_object_storage",
                return_value=mock_storage,
            ),
            patch.object(service, "_resolve_execution_timeout", return_value=None),
        ):
            await service._dispatch_workflow(
                dsl=dsl,
                wf_id=_WF_ID,
                wf_exec_id=f"{_WF_ID.short()}/exec_test",
                trigger_inputs={"x": 1},
                trigger_type=TriggerType.WEBHOOK,
            )

        call_kwargs = mock_client.execute_workflow.call_args
        search_attrs: TypedSearchAttributes = call_kwargs.kwargs["search_attributes"]
        trigger_key = TemporalSearchAttr.TRIGGER_TYPE.key
        found = [
            pair for pair in search_attrs.search_attributes if pair.key == trigger_key
        ]
        assert found and found[0].value == "webhook"
