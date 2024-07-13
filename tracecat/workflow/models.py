from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

import orjson
import temporalio.api.common.v1
import temporalio.api.enums.v1
import temporalio.api.history.v1
from google.protobuf.json_format import MessageToDict
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
)
from temporalio.client import WorkflowExecution, WorkflowExecutionStatus

from tracecat.dsl.workflow import UDFActionInput

WorkflowExecutionStatusLiteral = Literal[
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELED",
    "TERMINATED",
    "CONTINUED_AS_NEW",
    "TIMED_OUT",
]
"""Mapped literal types for workflow execution statuses."""


class EventHistoryType(StrEnum):
    """The event types we care about."""

    WORKFLOW_EXECUTION_STARTED = "WORKFLOW_EXECUTION_STARTED"
    WORKFLOW_EXECUTION_COMPLETED = "WORKFLOW_EXECUTION_COMPLETED"
    WORKFLOW_EXECUTION_FAILED = "WORKFLOW_EXECUTION_FAILED"
    ACTIVITY_TASK_SCHEDULED = "ACTIVITY_TASK_SCHEDULED"
    ACTIVITY_TASK_STARTED = "ACTIVITY_TASK_STARTED"
    ACTIVITY_TASK_COMPLETED = "ACTIVITY_TASK_COMPLETED"
    ACTIVITY_TASK_FAILED = "ACTIVITY_TASK_FAILED"


# EventHistoryAttributes = (
#     temporalio.api.history.v1.WorkflowExecutionStartedEventAttributes
#     | temporalio.api.history.v1.WorkflowExecutionCompletedEventAttributes
#     | temporalio.api.history.v1.WorkflowExecutionFailedEventAttributes
#     | temporalio.api.history.v1.ActivityTaskScheduledEventAttributes
#     | temporalio.api.history.v1.ActivityTaskStartedEventAttributes
#     | temporalio.api.history.v1.ActivityTaskCompletedEventAttributes
#     | temporalio.api.history.v1.ActivityTaskFailedEventAttributes
# )


class WorkflowExecutionResponse(BaseModel):
    id: str = Field(..., description="The ID of the workflow execution")
    run_id: str = Field(..., description="The run ID of the workflow execution")
    start_time: datetime = Field(
        ..., description="The start time of the workflow execution"
    )
    execution_time: datetime = Field(
        ..., description="When this workflow run started or should start."
    )
    close_time: datetime = Field(
        ..., description="When the workflow was closed if closed."
    )
    status: Annotated[
        WorkflowExecutionStatus | None,
        PlainSerializer(
            lambda x: x.name if x else None,
            return_type=WorkflowExecutionStatusLiteral,
            when_used="always",
        ),
    ]

    workflow_type: str
    task_queue: str
    history_length: int = Field(..., description="Number of events in the history")

    @staticmethod
    def from_dataclass(execution: WorkflowExecution) -> WorkflowExecutionResponse:
        return WorkflowExecutionResponse(
            id=execution.id,
            run_id=execution.run_id,
            start_time=execution.start_time,
            execution_time=execution.execution_time,
            close_time=execution.close_time,
            status=execution.status,
            workflow_type=execution.workflow_type,
            task_queue=execution.task_queue,
            history_length=execution.history_length,
        )


def destructure_slugified_namespace(s: str, delimiter: str = "__") -> tuple[str, str]:
    *stem, leaf = s.split(delimiter)
    return (".".join(stem), leaf)


class EventGroup(BaseModel):
    event_id: int
    udf_namespace: str
    udf_name: str
    udf_key: str
    action_id: str | None
    action_ref: str
    action_title: str
    action_description: str
    action_input: UDFActionInput
    action_result: Any | None = None

    @staticmethod
    def from_scheduled_activity(event: temporalio.api.history.v1.HistoryEvent):
        if (
            event.event_type
            != temporalio.api.enums.v1.EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        ):
            raise ValueError("Event is not an activity task scheduled event.")
        # Load the input data
        action_stmt_data = orjson.loads(
            event.activity_task_scheduled_event_attributes.input.payloads[0].data
        )
        action_input = UDFActionInput(**action_stmt_data)
        # Create an event group
        namespace, udf_name = destructure_slugified_namespace(
            action_input.task.action, delimiter="."
        )
        return EventGroup(
            event_id=event.event_id,
            udf_namespace=namespace,
            udf_name=udf_name,
            udf_key=action_input.task.action,
            action_id=action_input.task.id,
            action_ref=action_input.task.ref,
            action_title=action_input.task.title,
            action_description=action_input.task.description,
            action_input=action_input,
        )


class EventFailure(BaseModel):
    message: str
    stack_trace: str
    cause: dict[str, Any] | None = None
    application_failure_info: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def from_history_event(
        event: temporalio.api.history.v1.HistoryEvent,
    ) -> EventFailure:
        if (
            event.event_type
            == temporalio.api.enums.v1.EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED
        ):
            failure = event.activity_task_failed_event_attributes.failure
        elif (
            event.event_type
            == temporalio.api.enums.v1.EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED
        ):
            failure = event.workflow_execution_failed_event_attributes.failure
        else:
            raise ValueError("Event type not supported for failure extraction.")

        return EventFailure(
            message=failure.message,
            stack_trace=failure.stack_trace,
            cause=MessageToDict(failure.cause) if failure.cause is not None else None,
            application_failure_info=MessageToDict(failure.application_failure_info),
        )


class EventHistoryResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    event_id: int
    event_time: datetime
    event_type: EventHistoryType
    task_id: int
    event_group: EventGroup | None = Field(
        default=None,
        description="The action group of the event. We use this to keep track of what events are related to each other.",
    )
    failure: EventFailure | None = None
    result: Any | None = None
