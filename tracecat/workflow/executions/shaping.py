"""Shared workflow-execution shaping helpers for MCP tools and internal routers.

Builds the canonical summary/event projections defined in
``tracecat.workflow.executions.schemas`` from Temporal execution descriptions
and compact event histories. The event and summary shaping is shared verbatim;
the surrounding detail envelopes intentionally differ per transport (the MCP
tool returns the canonical detail projection, while the internal SDK route
wraps the same events in its legacy status envelope).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from temporalio.client import WorkflowExecution, WorkflowExecutionStatus

from tracecat.dsl.common import (
    get_execution_type_from_search_attr,
    get_trigger_type_from_search_attr,
)
from tracecat.workflow.executions.schemas import (
    WorkflowExecutionEventCompact,
    WorkflowExecutionEventError,
    WorkflowExecutionEventResponse,
    WorkflowExecutionSummaryResponse,
)

# Action results larger than this are returned as a truncated string instead of
# the raw value, to keep tool payloads within model context budgets.
MAX_EVENT_RESULT_CHARS = 2000


def format_temporal_status(status: WorkflowExecutionStatus | None) -> str | None:
    """Return a stable workflow status string for tool responses."""
    return status.name if status else None


def extract_execution_types(
    execution: WorkflowExecution,
) -> tuple[str | None, str | None]:
    """Return ``(trigger_type, execution_type)`` from an execution's search attributes.

    Search attributes are best-effort metadata: a missing or malformed attribute
    must not fail the whole listing, so extraction failures yield ``(None, None)``.
    """
    try:
        trigger_type = get_trigger_type_from_search_attr(
            execution.typed_search_attributes, execution.id
        )
        execution_type = get_execution_type_from_search_attr(
            execution.typed_search_attributes
        )
    except Exception:
        return None, None
    return (
        str(trigger_type) if trigger_type else None,
        str(execution_type) if execution_type else None,
    )


def build_execution_summary(
    execution: WorkflowExecution,
) -> WorkflowExecutionSummaryResponse:
    """Shape a Temporal execution into the canonical summary projection."""
    trigger_type, execution_type = extract_execution_types(execution)
    return WorkflowExecutionSummaryResponse(
        id=execution.id,
        run_id=execution.run_id,
        status=format_temporal_status(execution.status),
        start_time=str(execution.start_time),
        close_time=str(execution.close_time) if execution.close_time else None,
        trigger_type=trigger_type,
        execution_type=execution_type,
    )


def build_execution_event(
    event: WorkflowExecutionEventCompact[Any, Any, Any],
) -> WorkflowExecutionEventResponse:
    """Shape a compact execution event, truncating oversized action results."""
    # The status route excludes unset fields recursively. Set every canonical
    # event key explicitly so nullable event fields remain present on the wire.
    event_data = WorkflowExecutionEventResponse(
        action_ref=event.action_ref,
        action_name=event.action_name,
        status=str(event.status),
        schedule_time=str(event.schedule_time),
        start_time=str(event.start_time) if event.start_time else None,
        close_time=str(event.close_time) if event.close_time else None,
        error=None,
        result=None,
        result_truncated=None,
    )
    if event.action_error is not None:
        event_data.error = WorkflowExecutionEventError(
            message=event.action_error.message,
            cause=event.action_error.cause,
        )
    if event.action_result is not None:
        try:
            result_str = json.dumps(event.action_result, default=str)
            if len(result_str) > MAX_EVENT_RESULT_CHARS:
                event_data.result_truncated = (
                    result_str[:MAX_EVENT_RESULT_CHARS] + "..."
                )
            else:
                event_data.result = event.action_result
        except (TypeError, ValueError):
            event_data.result = str(event.action_result)[:MAX_EVENT_RESULT_CHARS]
    return event_data


def build_execution_events(
    events: Iterable[WorkflowExecutionEventCompact[Any, Any, Any]],
) -> list[WorkflowExecutionEventResponse]:
    """Shape a compact event history into the tool-facing event timeline."""
    return [build_execution_event(event) for event in events]
