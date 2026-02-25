from typing import Any

import orjson
import temporalio.api.common.v1
from pydantic import TypeAdapter, ValidationError
from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent

from tracecat.dsl.action import DSLActivities
from tracecat.dsl.compression import get_compression_payload_codec
from tracecat.executor.activities import ExecutorActivities
from tracecat.identifiers import UserID, WorkflowID
from tracecat.logger import logger
from tracecat.storage.object import (
    CollectionObject,
    ExternalObject,
    InlineObject,
    StoredObject,
)
from tracecat.workflow.executions.enums import (
    TemporalSearchAttr,
    TriggerType,
    WorkflowEventType,
)

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
    EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_CANCELED: WorkflowEventType.CHILD_WORKFLOW_EXECUTION_CANCELED,
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


# Activities that use action schemas (allowlist approach)
# These activities can be associated with action_ref in event history
# - execute_action_activity and noop_gather_action_activity use RunActionInput
# - handle_scatter_input_activity uses ScatterActionInput
ACTION_ACTIVITIES = {
    ExecutorActivities.execute_action_activity.__name__,
    DSLActivities.noop_gather_action_activity.__name__,
    DSLActivities.handle_scatter_input_activity.__name__,
}


def is_scheduled_event(event: HistoryEvent) -> bool:
    return event.event_type in SCHEDULED_EVENT_TYPES


def is_start_event(event: HistoryEvent) -> bool:
    return event.event_type in START_EVENT_TYPES


def is_close_event(event: HistoryEvent) -> bool:
    return event.event_type in CLOSE_EVENT_TYPES


def is_error_event(event: HistoryEvent) -> bool:
    return event.event_type in ERROR_EVENT_TYPES


def is_action_activity(activity_name: str) -> bool:
    """Check if the activity uses RunActionInput schema."""
    return activity_name in ACTION_ACTIVITIES


async def unwrap_action_result(task_result: StoredObject) -> Any:
    """Unwrap TaskResult and materialize StoredObject for display.

    With uniform envelope design, action results in Temporal history are stored as:
    TaskResult(result=StoredObject, result_typename=..., error=..., ...)

    This function extracts the actual data for UI display:
    - Validates StoredObject using TypeAdapter with discriminated union
    - For InlineObject: extracts 'data' directly
    - For ExternalObject: returns metadata for deferred retrieval
    - For CollectionObject: returns metadata (don't materialize huge collections)

    Returns the original value unchanged if it doesn't match TaskResult structure.
    """

    match task_result:
        case InlineObject(data=data):
            return data

        case ExternalObject():
            return task_result

        case CollectionObject():
            return task_result


_stored_object_validator: TypeAdapter[StoredObject] = TypeAdapter(StoredObject)


async def get_result(event: HistoryEvent) -> Any:
    """Extract and unwrap result from a completed workflow history event.

    For activity completions, this unwraps TaskResult/StoredObject to return
    the raw result data for UI display. Falls back to raw result for backward
    compatibility with pre-TaskResult workflow history.
    """
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

    result = await extract_first(payload)

    # Try to unwrap TaskResult/StoredObject for display
    # Fall back to raw result for backward compatibility
    logger.debug("Unwrapping result", result=result, type=type(result).__name__)
    try:
        task_result = _stored_object_validator.validate_python(result)
        return await unwrap_action_result(task_result)
    except ValidationError:
        # Pre-TaskResult format or non-action result - return as-is
        return result


async def get_stored_result(event: HistoryEvent) -> StoredObject | None:
    """Extract StoredObject from a completed workflow history event.

    Returns None when the event result cannot be validated as StoredObject
    (e.g. pre-uniform-envelope history entries).
    """
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

    result = await extract_first(payload)
    try:
        return _stored_object_validator.validate_python(result)
    except ValidationError:
        return None


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


async def extract_payload(
    payload: temporalio.api.common.v1.Payloads, index: int = 0
) -> Any:
    """Extract the first payload from a workflow history event."""
    # Always call the decoder. It will return the original payload if it's not compressed.
    # This enables backwards compatibility of newer payloads with older clients.
    codec = get_compression_payload_codec()
    decompressed_payload = await codec.decode(payload.payloads)
    payload_obj = decompressed_payload[index]
    encoding = payload_obj.metadata.get("encoding", b"").decode()
    # Temporal's NullPayloadConverter encodes `None` as binary/null with no data.
    if encoding == "binary/null":
        logger.debug("Decoded binary/null payload; returning None")
        return None

    raw_data = payload_obj.data
    # Empty payload bytes should round-trip to Python None, not an empty string
    if not raw_data:
        logger.debug("Decoded payload is empty; returning None")
        return None
    try:
        return orjson.loads(raw_data)
    except orjson.JSONDecodeError as e:
        logger.debug(
            "Failed to decode JSON data, attemping to decode as string",
            raw_data=raw_data,
            e=e,
        )

    try:
        text = raw_data.decode()
        if text.strip() == "" or text.strip().lower() == "null":
            return None
        return text
    except UnicodeDecodeError:
        logger.debug("Failed to decode data as string, returning raw bytes")
        return raw_data


async def extract_first(input_or_result: temporalio.api.common.v1.Payloads) -> Any:
    """Extract the first payload from a workflow history event."""
    return await extract_payload(input_or_result, index=0)


def build_query(
    workflow_id: WorkflowID | None = None,
    trigger_types: set[TriggerType] | None = None,
    triggered_by_user_id: UserID | None = None,
    workspace_id: str | None = None,
    _include_legacy: bool = True,
) -> str:
    query = []
    if workspace_id:
        query.append(f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{workspace_id}'")
    if workflow_id:
        short_id = workflow_id.short()
        wf_id_query = f"WorkflowId STARTS_WITH '{short_id}'"
        if _include_legacy:
            # NOTE(COMPAT): Include legacy workflow ID for backwards compatibility
            legacy_wf_id = workflow_id.to_legacy()
            wf_id_query += f" OR WorkflowId STARTS_WITH '{legacy_wf_id}'"
        query.append(f"({wf_id_query})")
    trigger_type_query = []
    if trigger_types:
        for trigger_type in trigger_types:
            if trigger_type == TriggerType.MANUAL:
                # Manual trigger type is a special case that requires a user ID
                if triggered_by_user_id is not None:
                    trigger_type_query.append(
                        f"({TemporalSearchAttr.TRIGGER_TYPE.value} = '{TriggerType.MANUAL}' AND {TemporalSearchAttr.TRIGGERED_BY_USER_ID.value} = '{str(triggered_by_user_id)}')"
                    )
                else:
                    logger.warning(
                        "Manual trigger type specified but no user ID provided. This is likely a bug.",
                        workflow_id=workflow_id,
                    )
            else:
                # All other trigger types are simple
                trigger_type_query.append(
                    f"({TemporalSearchAttr.TRIGGER_TYPE.value} = '{trigger_type.value}')"
                )
        if trigger_type_query:
            query.append(f"({' OR '.join(trigger_type_query)})")
    return " AND ".join(query)
