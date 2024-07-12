from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

import google.protobuf.json_format
import temporalio.api.common.v1
import temporalio.api.enums.v1
import temporalio.api.history.v1
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_serializer,
)
from temporalio.client import WorkflowExecution, WorkflowExecutionStatus

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


EventHistoryAttributes = (
    temporalio.api.history.v1.WorkflowExecutionStartedEventAttributes
    | temporalio.api.history.v1.WorkflowExecutionCompletedEventAttributes
    | temporalio.api.history.v1.WorkflowExecutionFailedEventAttributes
    | temporalio.api.history.v1.ActivityTaskScheduledEventAttributes
    | temporalio.api.history.v1.ActivityTaskStartedEventAttributes
    | temporalio.api.history.v1.ActivityTaskCompletedEventAttributes
    | temporalio.api.history.v1.ActivityTaskFailedEventAttributes
)


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


class EventHistoryResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    event_id: int
    event_time: datetime
    event_type: EventHistoryType
    task_id: int
    details: EventHistoryAttributes

    @field_serializer("details")
    def serialize_details(v: EventHistoryAttributes) -> Any:
        return google.protobuf.json_format.MessageToDict(v)
