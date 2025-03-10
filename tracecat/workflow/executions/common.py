from typing import Any

import orjson
import temporalio.api.common.v1
from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent

from tracecat.identifiers import UserID, WorkflowID
from tracecat.logger import logger
from tracecat.workflow.executions.enums import (
    TemporalSearchAttr,
    TriggerType,
    WorkflowEventType,
)
from tracecat.workflow.management.management import WorkflowsManagementService

SCHEDULED_EVENT_TYPES = (
    EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,
    EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED,
)

START_EVENT_TYPES = (
    EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED,
    EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED,  # This is both a start and a scheduled event
)

CLOSE_EVENT_TYPES = (
    EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,
    EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_COMPLETED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_COMPLETED,
)

ERROR_EVENT_TYPES = (
    EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED,
    EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_FAILED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_REJECTED,
)

HISTORY_TO_WF_EVENT_TYPE = {
    # Activity Task
    EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED: WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
    EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED: WorkflowEventType.ACTIVITY_TASK_STARTED,
    EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED: WorkflowEventType.ACTIVITY_TASK_COMPLETED,
    EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED: WorkflowEventType.ACTIVITY_TASK_FAILED,
    EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT: WorkflowEventType.ACTIVITY_TASK_TIMED_OUT,
    EventType.EVENT_TYPE_ACTIVITY_TASK_CANCELED: WorkflowEventType.ACTIVITY_TASK_CANCELED,
    # Child Workflow
    EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED: WorkflowEventType.START_CHILD_WORKFLOW_EXECUTION_INITIATED,
    EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED: WorkflowEventType.CHILD_WORKFLOW_EXECUTION_STARTED,
    EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_COMPLETED: WorkflowEventType.CHILD_WORKFLOW_EXECUTION_COMPLETED,
    EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_FAILED: WorkflowEventType.CHILD_WORKFLOW_EXECUTION_FAILED,
    EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_TIMED_OUT: WorkflowEventType.CHILD_WORKFLOW_EXECUTION_TIMED_OUT,
    # Workflow
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED: WorkflowEventType.WORKFLOW_EXECUTION_STARTED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED: WorkflowEventType.WORKFLOW_EXECUTION_COMPLETED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED: WorkflowEventType.WORKFLOW_EXECUTION_FAILED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_TERMINATED: WorkflowEventType.WORKFLOW_EXECUTION_TERMINATED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CANCELED: WorkflowEventType.WORKFLOW_EXECUTION_CANCELED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW: WorkflowEventType.WORKFLOW_EXECUTION_CONTINUED_AS_NEW,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_TIMED_OUT: WorkflowEventType.WORKFLOW_EXECUTION_TIMED_OUT,
    # Workflow Update
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED: WorkflowEventType.WORKFLOW_EXECUTION_UPDATE_ACCEPTED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_COMPLETED: WorkflowEventType.WORKFLOW_EXECUTION_UPDATE_COMPLETED,
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_REJECTED: WorkflowEventType.WORKFLOW_EXECUTION_UPDATE_REJECTED,
}


UTILITY_ACTIONS = {
    "get_schedule_activity",
    "validate_trigger_inputs_activity",
    "validate_action_activity",
    "parse_wait_until_activity",
    WorkflowsManagementService.resolve_workflow_alias_activity.__name__,
    WorkflowsManagementService.get_error_handler_workflow_id.__name__,
}


def is_scheduled_event(event: HistoryEvent) -> bool:
    return event.event_type in SCHEDULED_EVENT_TYPES


def is_start_event(event: HistoryEvent) -> bool:
    return event.event_type in START_EVENT_TYPES


def is_close_event(event: HistoryEvent) -> bool:
    return event.event_type in CLOSE_EVENT_TYPES


def is_error_event(event: HistoryEvent) -> bool:
    return event.event_type in ERROR_EVENT_TYPES


def is_utility_activity(activity_name: str) -> bool:
    return activity_name in UTILITY_ACTIONS


def get_result(event: HistoryEvent) -> Any:
    match event.event_type:
        case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED:
            payload = event.workflow_execution_completed_event_attributes.result
        case EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            payload = event.activity_task_completed_event_attributes.result
        case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_COMPLETED:
            payload = event.child_workflow_execution_completed_event_attributes.result
        case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_COMPLETED:
            payload = event.workflow_execution_update_completed_event_attributes.outcome.success
        case _:
            raise ValueError("Event is not a completed event")
    return extract_first(payload)


def get_source_event_id(event: HistoryEvent) -> int | None:
    match event.event_type:
        case EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED:
            return event.activity_task_started_event_attributes.scheduled_event_id
        case EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            return event.activity_task_completed_event_attributes.scheduled_event_id
        case EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED:
            return event.activity_task_failed_event_attributes.scheduled_event_id
        case EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT:
            return event.activity_task_timed_out_event_attributes.scheduled_event_id
        case EventType.EVENT_TYPE_ACTIVITY_TASK_CANCELED:
            return event.activity_task_canceled_event_attributes.scheduled_event_id
        case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED:
            return event.child_workflow_execution_started_event_attributes.initiated_event_id
        case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_CANCELED:
            return event.child_workflow_execution_canceled_event_attributes.initiated_event_id
        case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_COMPLETED:
            return event.child_workflow_execution_completed_event_attributes.initiated_event_id
        case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_FAILED:
            return event.child_workflow_execution_failed_event_attributes.initiated_event_id
        case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_TIMED_OUT:
            return event.child_workflow_execution_timed_out_event_attributes.initiated_event_id
        case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_COMPLETED:
            return event.workflow_execution_update_completed_event_attributes.accepted_event_id
        case _:
            return None


def extract_payload(payload: temporalio.api.common.v1.Payloads, index: int = 0) -> Any:
    """Extract the first payload from a workflow history event."""
    raw_data = payload.payloads[index].data
    try:
        return orjson.loads(raw_data)
    except orjson.JSONDecodeError as e:
        logger.warning(
            "Failed to decode JSON data, attemping to decode as string",
            raw_data=raw_data,
            e=e,
        )

    try:
        return raw_data.decode()
    except UnicodeDecodeError:
        logger.warning("Failed to decode data as string, returning raw bytes")
        return raw_data


def extract_first(input_or_result: temporalio.api.common.v1.Payloads) -> Any:
    """Extract the first payload from a workflow history event."""
    return extract_payload(input_or_result, index=0)


def build_query(
    workflow_id: WorkflowID | None = None,
    trigger_types: set[TriggerType] | None = None,
    triggered_by_user_id: UserID | None = None,
    _include_legacy: bool = True,
) -> str:
    query = []
    if workflow_id:
        short_id = workflow_id.short()
        wf_id_query = f"WorkflowId STARTS_WITH '{short_id}'"
        if _include_legacy:
            # NOTE(COMPAT): Include legacy workflow ID for backwards compatibility
            legacy_wf_id = workflow_id.to_legacy()
            wf_id_query += f" OR WorkflowId STARTS_WITH '{legacy_wf_id}'"
        query.append(f"({wf_id_query})")
    if trigger_types:
        if len(trigger_types) == 1:
            query.append(
                f"{TemporalSearchAttr.TRIGGER_TYPE.value} = '{trigger_types.pop().value}'"
            )
        else:
            query.append(
                f"{TemporalSearchAttr.TRIGGER_TYPE.value} IN ({', '.join(f"'{t.value}'" for t in trigger_types)})"
            )
    if triggered_by_user_id is not None:
        query.append(
            f"{TemporalSearchAttr.TRIGGERED_BY_USER_ID.value} = '{str(triggered_by_user_id)}'"
        )
    return " AND ".join(query)
