"""Unit tests for resolving draft action pins from prior executions."""

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import ActionStatement, TaskResult
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.storage.object import ExternalObject, InlineObject, ObjectRef
from tracecat.workflow.executions.enums import (
    WorkflowEventType,
    WorkflowExecutionEventStatus,
)
from tracecat.workflow.executions.schemas import WorkflowExecutionEventCompact
from tracecat.workflow.executions.service import (
    PinnedRunContext,
    WorkflowExecutionsService,
)
from tracecat.workflow.management.types import WorkflowDraftPinsData


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


def _detail(exc: TracecatValidationError) -> dict[str, Any]:
    """Machine-readable rejection detail; assertions never read message text."""
    detail = exc.detail
    assert isinstance(detail, dict)
    return cast(dict[str, Any], detail)


def _pins(source_execution_id: str, *action_refs: str) -> WorkflowDraftPinsData:
    return WorkflowDraftPinsData(
        source_execution_id=source_execution_id,
        action_refs=list(action_refs),
    )


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
async def test_scatter_body_ref_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: actions inside scatter scopes cannot supply draft pins."""
    wf_id = WorkflowUUID.new_uuid4()
    source_execution_id = generate_exec_id(wf_id)
    service = _service()
    monkeypatch.setattr(service, "get_execution", AsyncMock(return_value=MagicMock()))
    list_events = AsyncMock(return_value=[_event("body")])
    monkeypatch.setattr(service, "list_workflow_execution_events_compact", list_events)

    result = await service.resolve_draft_pinned_action_results(
        wf_id=wf_id,
        dsl=_dsl(
            ActionStatement(ref="prepare", action="core.noop"),
            ActionStatement(
                ref="scatter",
                action=PlatformAction.TRANSFORM_SCATTER,
                args={"collection": "${{ ACTIONS.prepare.result }}"},
                depends_on=["prepare"],
            ),
            ActionStatement(ref="body", action="core.noop", depends_on=["scatter"]),
            ActionStatement(
                ref="gather",
                action=PlatformAction.TRANSFORM_GATHER,
                args={"items": "${{ ACTIONS.body.result }}"},
                depends_on=["body"],
            ),
        ),
        draft_pins=_pins(source_execution_id, "body"),
    )

    assert result == {}
    list_events.assert_not_awaited()


@pytest.mark.anyio
async def test_loop_body_ref_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: actions inside loop scopes cannot supply draft pins."""
    wf_id = WorkflowUUID.new_uuid4()
    source_execution_id = generate_exec_id(wf_id)
    service = _service()
    monkeypatch.setattr(service, "get_execution", AsyncMock(return_value=MagicMock()))
    list_events = AsyncMock(return_value=[_event("body")])
    monkeypatch.setattr(service, "list_workflow_execution_events_compact", list_events)

    result = await service.resolve_draft_pinned_action_results(
        wf_id=wf_id,
        dsl=_dsl(
            ActionStatement(ref="loop_start", action=PlatformAction.LOOP_START),
            ActionStatement(ref="body", action="core.noop", depends_on=["loop_start"]),
            ActionStatement(
                ref="loop_end",
                action=PlatformAction.LOOP_END,
                args={"condition": "${{ False }}"},
                depends_on=["body"],
            ),
        ),
        draft_pins=_pins(source_execution_id, "body"),
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
async def test_current_masked_action_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: the current draft's mask_output blocks an unmasked source pin."""
    wf_id = WorkflowUUID.new_uuid4()
    source_execution_id = generate_exec_id(wf_id)
    service = _service()
    monkeypatch.setattr(service, "get_execution", AsyncMock(return_value=MagicMock()))
    list_events = AsyncMock(return_value=[_event("a")])
    monkeypatch.setattr(service, "list_workflow_execution_events_compact", list_events)

    result = await service.resolve_draft_pinned_action_results(
        wf_id=wf_id,
        dsl=_dsl(ActionStatement(ref="a", action="core.noop", mask_output=True)),
        draft_pins=_pins(source_execution_id, "a"),
    )

    assert result == {}
    list_events.assert_not_awaited()


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


@pytest.mark.anyio
async def test_raw_task_result_shaped_dict_remains_user_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: raw dicts are not inferred to be TaskResult envelopes."""
    wf_id = WorkflowUUID.new_uuid4()
    source_execution_id = generate_exec_id(wf_id)
    service = _service()
    monkeypatch.setattr(service, "get_execution", AsyncMock(return_value=MagicMock()))
    raw_result = {
        "result": {"type": "inline", "data": "inner"},
        "result_typename": "dict",
    }
    event = _event("a")
    event.action_result = raw_result
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

    assert result["a"].get_data() == raw_result


@pytest.mark.anyio
async def test_external_object_instance_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: a typed external result remains the TaskResult payload."""
    wf_id = WorkflowUUID.new_uuid4()
    source_execution_id = generate_exec_id(wf_id)
    service = _service()
    monkeypatch.setattr(service, "get_execution", AsyncMock(return_value=MagicMock()))
    external = ExternalObject(
        ref=ObjectRef(
            bucket="test-bucket",
            key="wf/test/result.json",
            size_bytes=128,
            sha256="abc123",
        ),
        typename="dict",
    )
    event = _event("a")
    event.action_result = external
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

    assert result["a"].result is external


@pytest.mark.anyio
async def test_stitching_preserves_original_pin_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: repinning keeps the first execution and event provenance."""
    wf_id = WorkflowUUID.new_uuid4()
    execution_a = generate_exec_id(wf_id)
    execution_b = generate_exec_id(wf_id)
    execution_c = generate_exec_id(wf_id)
    service = _service()
    assert service.role is not None
    dsl = _dsl(ActionStatement(ref="a", action="core.noop"))
    run_args = DSLRunArgs(
        role=service.role,
        dsl=dsl,
        wf_id=wf_id,
        pinned_action_results={"a": TaskResult.from_result("pinned")},
        pinned_source_execution_id=execution_b,
    )
    run_context = PinnedRunContext(
        run_args=run_args,
        started_at=datetime.now(UTC),
        skipped_pinned_refs=frozenset(),
    )
    monkeypatch.setattr(
        service, "_get_pinned_run_context", AsyncMock(return_value=run_context)
    )
    source_event = _event("a")
    source_event.synthetic_kind = "pinned"
    source_event.pinned_source_execution_id = execution_a
    source_event.pinned_source_event_id = 7
    monkeypatch.setattr(
        service,
        "list_workflow_execution_events_compact",
        AsyncMock(return_value=[source_event]),
    )

    stitched = await service._stitch_pinned_compact_events(
        wf_exec_id=execution_c,
        compact_events=[],
    )

    assert len(stitched) == 1
    assert stitched[0].pinned_source_execution_id == execution_a
    assert stitched[0].pinned_source_event_id == 7


# ---------------------------------------------------------------------------
# resolve_run_from_action_pins
# ---------------------------------------------------------------------------


def _chain_dsl() -> DSLInput:
    """a -> b -> c, all root-scope success edges."""
    return _dsl(
        ActionStatement(ref="a", action="core.noop"),
        ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
        ActionStatement(ref="c", action="core.noop", depends_on=["b"]),
    )


@pytest.mark.anyio
async def test_run_from_action_pins_only_immediate_parents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: the cut set is exactly the selected action's parents."""
    wf_id = WorkflowUUID.new_uuid4()
    source_execution_id = generate_exec_id(wf_id)
    service = _service()
    monkeypatch.setattr(service, "get_execution", AsyncMock(return_value=MagicMock()))
    list_events = AsyncMock(return_value=[_event("a"), _event("b")])
    monkeypatch.setattr(service, "list_workflow_execution_events_compact", list_events)

    result = await service.resolve_run_from_action_pins(
        wf_id=wf_id,
        dsl=_chain_dsl(),
        action_ref="c",
        source_execution_id=source_execution_id,
    )

    assert set(result) == {"b"}
    list_events.assert_awaited_once()


@pytest.mark.anyio
async def test_run_from_action_rejects_unknown_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: a ref outside the current draft graph cannot start a run."""
    wf_id = WorkflowUUID.new_uuid4()
    service = _service()
    list_events = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "list_workflow_execution_events_compact", list_events)

    with pytest.raises(TracecatValidationError) as exc_info:
        await service.resolve_run_from_action_pins(
            wf_id=wf_id,
            dsl=_chain_dsl(),
            action_ref="ghost",
            source_execution_id=generate_exec_id(wf_id),
        )

    assert _detail(exc_info.value)["code"] == "unknown_action_ref"
    list_events.assert_not_awaited()


@pytest.mark.anyio
async def test_run_from_action_rejects_scoped_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: actions inside a scatter scope are not restartable."""
    wf_id = WorkflowUUID.new_uuid4()
    service = _service()
    list_events = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "list_workflow_execution_events_compact", list_events)

    dsl = _dsl(
        ActionStatement(ref="prepare", action="core.noop"),
        ActionStatement(
            ref="scatter",
            action=PlatformAction.TRANSFORM_SCATTER,
            args={"collection": "${{ ACTIONS.prepare.result }}"},
            depends_on=["prepare"],
        ),
        ActionStatement(ref="body", action="core.noop", depends_on=["scatter"]),
        ActionStatement(
            ref="gather",
            action=PlatformAction.TRANSFORM_GATHER,
            args={"items": "${{ ACTIONS.body.result }}"},
            depends_on=["body"],
        ),
    )

    with pytest.raises(TracecatValidationError) as exc_info:
        await service.resolve_run_from_action_pins(
            wf_id=wf_id,
            dsl=dsl,
            action_ref="body",
            source_execution_id=generate_exec_id(wf_id),
        )

    assert _detail(exc_info.value)["code"] == "action_not_restartable"
    list_events.assert_not_awaited()


@pytest.mark.anyio
async def test_run_from_action_rejects_error_edge_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: a failed parent result cannot be reused as a pinned success."""
    wf_id = WorkflowUUID.new_uuid4()
    service = _service()
    list_events = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "list_workflow_execution_events_compact", list_events)

    dsl = _dsl(
        ActionStatement(ref="a", action="core.noop"),
        ActionStatement(ref="handler", action="core.noop", depends_on=["a.error"]),
    )

    with pytest.raises(TracecatValidationError) as exc_info:
        await service.resolve_run_from_action_pins(
            wf_id=wf_id,
            dsl=dsl,
            action_ref="handler",
            source_execution_id=generate_exec_id(wf_id),
        )

    assert _detail(exc_info.value)["code"] == "error_edge_parent"
    assert _detail(exc_info.value)["parent_refs"] == ["a"]
    list_events.assert_not_awaited()


@pytest.mark.anyio
async def test_run_from_action_rejects_control_flow_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: a gather result cannot be reused as an upstream pin."""
    wf_id = WorkflowUUID.new_uuid4()
    service = _service()
    list_events = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "list_workflow_execution_events_compact", list_events)

    dsl = _dsl(
        ActionStatement(ref="prepare", action="core.noop"),
        ActionStatement(
            ref="scatter",
            action=PlatformAction.TRANSFORM_SCATTER,
            args={"collection": "${{ ACTIONS.prepare.result }}"},
            depends_on=["prepare"],
        ),
        ActionStatement(ref="body", action="core.noop", depends_on=["scatter"]),
        ActionStatement(
            ref="gather",
            action=PlatformAction.TRANSFORM_GATHER,
            args={"items": "${{ ACTIONS.body.result }}"},
            depends_on=["body"],
        ),
        ActionStatement(ref="after", action="core.noop", depends_on=["gather"]),
    )

    with pytest.raises(TracecatValidationError) as exc_info:
        await service.resolve_run_from_action_pins(
            wf_id=wf_id,
            dsl=dsl,
            action_ref="after",
            source_execution_id=generate_exec_id(wf_id),
        )

    assert _detail(exc_info.value)["code"] == "control_flow_parent"
    assert _detail(exc_info.value)["parent_refs"] == ["gather"]
    list_events.assert_not_awaited()


@pytest.mark.anyio
async def test_run_from_action_requires_every_parent_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: a missing parent result fails instead of re-running upstream."""
    wf_id = WorkflowUUID.new_uuid4()
    source_execution_id = generate_exec_id(wf_id)
    service = _service()
    monkeypatch.setattr(service, "get_execution", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(
        service,
        "list_workflow_execution_events_compact",
        AsyncMock(return_value=[]),
    )

    with pytest.raises(TracecatValidationError) as exc_info:
        await service.resolve_run_from_action_pins(
            wf_id=wf_id,
            dsl=_chain_dsl(),
            action_ref="c",
            source_execution_id=source_execution_id,
        )

    assert _detail(exc_info.value)["code"] == "unresolved_parents"
    assert _detail(exc_info.value)["parent_refs"] == ["b"]


@pytest.mark.anyio
async def test_run_from_root_action_needs_no_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A root action needs no pins but still requires source visibility."""
    wf_id = WorkflowUUID.new_uuid4()
    service = _service()
    list_events = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "list_workflow_execution_events_compact", list_events)

    require = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(service, "require_execution", require)
    result = await service.resolve_run_from_action_pins(
        wf_id=wf_id,
        dsl=_chain_dsl(),
        action_ref="a",
        source_execution_id=generate_exec_id(wf_id),
    )

    assert result == {}
    require.assert_awaited_once()
    list_events.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_execution_trigger_inputs
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_execution_trigger_inputs_returns_inline_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: inline trigger inputs are replayed verbatim."""
    wf_id = WorkflowUUID.new_uuid4()
    service = _service()
    monkeypatch.setattr(service, "require_execution", AsyncMock())
    assert service.role is not None
    run_args = DSLRunArgs(
        role=service.role,
        dsl=_chain_dsl(),
        wf_id=wf_id,
        trigger_inputs=InlineObject(data={"alpha": 1}),
    )
    monkeypatch.setattr(
        service, "_get_run_start_args", AsyncMock(return_value=run_args)
    )

    assert await service.get_execution_trigger_inputs(generate_exec_id(wf_id)) == {
        "alpha": 1
    }


@pytest.mark.anyio
async def test_execution_trigger_inputs_rejects_external_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: offloaded trigger inputs must be supplied by the caller."""
    wf_id = WorkflowUUID.new_uuid4()
    service = _service()
    monkeypatch.setattr(service, "require_execution", AsyncMock())
    assert service.role is not None
    run_args = DSLRunArgs(
        role=service.role,
        dsl=_chain_dsl(),
        wf_id=wf_id,
        trigger_inputs=ExternalObject(
            ref=ObjectRef(
                bucket="test-bucket",
                key="wf/test/trigger.json",
                size_bytes=64,
                sha256="abc123",
            ),
            typename="dict",
        ),
    )
    monkeypatch.setattr(
        service, "_get_run_start_args", AsyncMock(return_value=run_args)
    )

    with pytest.raises(TracecatValidationError) as exc_info:
        await service.get_execution_trigger_inputs(generate_exec_id(wf_id))

    assert _detail(exc_info.value)["code"] == "source_inputs_not_inline"


@pytest.mark.anyio
async def test_root_replay_rejects_other_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    require = AsyncMock()
    monkeypatch.setattr(service, "require_execution", require)
    with pytest.raises(TracecatValidationError) as exc:
        await service.resolve_run_from_action_pins(
            wf_id=WorkflowUUID.new_uuid4(),
            dsl=_chain_dsl(),
            action_ref="a",
            source_execution_id=generate_exec_id(WorkflowUUID.new_uuid4()),
        )
    assert _detail(exc.value)["code"] == "source_workflow_mismatch"
    require.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("read_inputs", [False, True])
async def test_replay_denies_invisible_source_before_history(
    monkeypatch: pytest.MonkeyPatch,
    read_inputs: bool,
) -> None:
    service = _service()
    wf_id = WorkflowUUID.new_uuid4()
    source_id = generate_exec_id(wf_id)
    monkeypatch.setattr(service, "get_execution", AsyncMock(return_value=None))
    history = AsyncMock()
    monkeypatch.setattr(service, "_get_run_start_args", history)
    with pytest.raises(TracecatNotFoundError):
        if read_inputs:
            await service.get_execution_trigger_inputs(source_id)
        else:
            await service.resolve_run_from_action_pins(
                wf_id=wf_id,
                dsl=_chain_dsl(),
                action_ref="a",
                source_execution_id=source_id,
            )
    history.assert_not_awaited()
