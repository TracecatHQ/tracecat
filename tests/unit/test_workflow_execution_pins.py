"""Unit tests for pinned draft-run event stitching helpers."""

from datetime import UTC, datetime

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.workflow.executions.constants import WF_COMPLETED_REF, WF_TRIGGER_REF
from tracecat.workflow.executions.enums import (
    WorkflowEventType,
    WorkflowExecutionEventStatus,
)
from tracecat.workflow.executions.schemas import WorkflowExecutionEventCompact
from tracecat.workflow.executions.service import WorkflowExecutionsService


def _event(action_ref: str, source_event_id: int) -> WorkflowExecutionEventCompact:
    now = datetime.now(UTC)
    return WorkflowExecutionEventCompact(
        source_event_id=source_event_id,
        schedule_time=now,
        curr_event_type=WorkflowEventType.WORKFLOW_EXECUTION_STARTED,
        status=WorkflowExecutionEventStatus.COMPLETED,
        action_name=action_ref,
        action_ref=action_ref,
    )


def _fan_in_dsl() -> DSLInput:
    return DSLInput(
        title="pin-order",
        description="pin-order",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(ref="a", action="core.noop"),
            ActionStatement(ref="b", action="core.noop", depends_on=["a"]),
            ActionStatement(ref="c", action="core.noop", depends_on=["b"]),
            ActionStatement(ref="d", action="core.noop", depends_on=["a"]),
            ActionStatement(ref="e", action="core.noop", depends_on=["c", "d"]),
        ],
    )


def test_dag_order_keeps_trigger_first_and_terminal_row_last() -> None:
    """Invariant: stitched pinned rows land in DAG position between the real
    action rows, while the trigger row stays first and the workflow result row
    stays last, whatever order they were appended in."""
    real_rows = [
        _event(WF_TRIGGER_REF, 1),
        _event("a", 2),
        _event("d", 3),
        _event("e", 4),
        _event(WF_COMPLETED_REF, 5),
    ]
    stitched_c = _event("c", 99)
    stitched_c.synthetic_kind = "pinned"

    ordered = WorkflowExecutionsService._order_compact_events_by_dag(
        [*real_rows, stitched_c], _fan_in_dsl()
    )

    # Breadth-first topological order: a, then b/d, then c, then e.
    assert [event.action_ref for event in ordered] == [
        WF_TRIGGER_REF,
        "a",
        "d",
        "c",
        "e",
        WF_COMPLETED_REF,
    ]


def test_dag_order_with_every_action_pinned_still_brackets_with_trigger_and_result() -> (
    None
):
    """Invariant: when only stitched rows exist between the trigger and the
    terminal row, the terminal row is not mistaken for a leading row."""
    rows = [
        _event(WF_TRIGGER_REF, 1),
        _event(WF_COMPLETED_REF, 2),
        _event("d", 90),
        _event("c", 91),
    ]

    ordered = WorkflowExecutionsService._order_compact_events_by_dag(
        rows, _fan_in_dsl()
    )

    assert [event.action_ref for event in ordered] == [
        WF_TRIGGER_REF,
        "d",
        "c",
        WF_COMPLETED_REF,
    ]
