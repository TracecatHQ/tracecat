"""Unit tests for resolving draft action pins from prior executions."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import ActionStatement
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.workflow.executions.enums import (
    WorkflowEventType,
    WorkflowExecutionEventStatus,
)
from tracecat.workflow.executions.schemas import WorkflowExecutionEventCompact
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management.schemas import WorkflowDraftPins


def _service() -> WorkflowExecutionsService:
    role = Role(
        type="service",
        service_id="tracecat-service",
        workspace_id=WorkflowUUID.new_uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
    )
    return WorkflowExecutionsService(client=MagicMock(spec=Client), role=role)


def _dsl(*actions: ActionStatement) -> DSLInput:
    return DSLInput(
        title="draft-pin-resolution",
        description="draft-pin-resolution",
        entrypoint=DSLEntrypoint(ref=actions[0].ref),
        actions=list(actions),
    )


def _event(action_ref: str) -> WorkflowExecutionEventCompact:
    return WorkflowExecutionEventCompact(
        source_event_id=1,
        schedule_time=datetime.now(UTC),
        curr_event_type=WorkflowEventType.ACTIVITY_TASK_COMPLETED,
        status=WorkflowExecutionEventStatus.COMPLETED,
        action_name="core.noop",
        action_ref=action_ref,
        action_result={"value": "source"},
    )


def _pins(source_execution_id: str, *action_refs: str) -> dict[str, object]:
    return WorkflowDraftPins(
        source_execution_id=source_execution_id,
        action_refs=list(action_refs),
    ).model_dump(mode="json")


@pytest.mark.anyio
async def test_control_flow_ref_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: control-flow actions cannot supply reusable draft pin results."""
    wf_id = WorkflowUUID.new_uuid4()
    source_execution_id = generate_exec_id(wf_id)
    service = _service()
    monkeypatch.setattr(service, "get_execution", AsyncMock(return_value=MagicMock()))
    list_events = AsyncMock(return_value=[_event("scatter")])
    monkeypatch.setattr(service, "list_workflow_execution_events_compact", list_events)

    result = await service.resolve_draft_pinned_action_results(
        wf_id=wf_id,
        dsl=_dsl(
            ActionStatement(ref="scatter", action=PlatformAction.TRANSFORM_SCATTER)
        ),
        draft_pins=_pins(source_execution_id, "scatter"),
    )

    assert result == {}
    list_events.assert_not_awaited()


@pytest.mark.anyio
async def test_masked_result_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: actions with masked output cannot supply draft pin results."""
    wf_id = WorkflowUUID.new_uuid4()
    source_execution_id = generate_exec_id(wf_id)
    service = _service()
    monkeypatch.setattr(service, "get_execution", AsyncMock(return_value=MagicMock()))
    event = _event("a")
    event.set_mask_output(True)
    monkeypatch.setattr(
        service,
        "list_workflow_execution_events_compact",
        AsyncMock(return_value=[event]),
    )

    result = await service.resolve_draft_pinned_action_results(
        wf_id=wf_id,
        dsl=_dsl(ActionStatement(ref="a", action="core.noop")),
        draft_pins=_pins(source_execution_id, "a"),
    )

    assert result == {}


@pytest.mark.anyio
async def test_source_lookup_rpc_error_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: Temporal source lookup failures disable pins without failing."""
    wf_id = WorkflowUUID.new_uuid4()
    source_execution_id = generate_exec_id(wf_id)
    service = _service()
    monkeypatch.setattr(
        service,
        "get_execution",
        AsyncMock(
            side_effect=RPCError("Temporal unavailable", RPCStatusCode.UNAVAILABLE, b"")
        ),
    )

    result = await service.resolve_draft_pinned_action_results(
        wf_id=wf_id,
        dsl=_dsl(ActionStatement(ref="a", action="core.noop")),
        draft_pins=_pins(source_execution_id, "a"),
    )

    assert result == {}


@pytest.mark.anyio
async def test_resolver_includes_synthetic_pinned_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: draft pins can resolve through synthetic rows from earlier runs."""
    wf_id = WorkflowUUID.new_uuid4()
    source_execution_id = generate_exec_id(wf_id)
    service = _service()
    monkeypatch.setattr(service, "get_execution", AsyncMock(return_value=MagicMock()))
    list_events = AsyncMock(return_value=[_event("a")])
    monkeypatch.setattr(service, "list_workflow_execution_events_compact", list_events)

    result = await service.resolve_draft_pinned_action_results(
        wf_id=wf_id,
        dsl=_dsl(ActionStatement(ref="a", action="core.noop")),
        draft_pins=_pins(source_execution_id, "a"),
    )

    assert result["a"].get_data() == {"value": "source"}
    list_events.assert_awaited_once_with(
        source_execution_id, include_pinned_synthetic=True
    )
