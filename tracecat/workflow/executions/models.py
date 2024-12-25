from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Generic, Literal, TypedDict, TypeVar, cast

import orjson
import temporalio.api.common.v1
import temporalio.api.enums.v1
import temporalio.api.history.v1
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from temporalio.client import WorkflowExecution, WorkflowExecutionStatus

from tracecat.dsl.common import DSLRunArgs
from tracecat.dsl.enums import JoinStrategy
from tracecat.dsl.models import ActionRetryPolicy, RunActionInput, TriggerInputs
from tracecat.identifiers import WorkflowExecutionID, WorkflowID
from tracecat.types.auth import Role
from tracecat.workflow.management.models import GetWorkflowDefinitionActivityInputs

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
    WORKFLOW_EXECUTION_TERMINATED = "WORKFLOW_EXECUTION_TERMINATED"
    WORKFLOW_EXECUTION_CANCELED = "WORKFLOW_EXECUTION_CANCELED"
    WORKFLOW_EXECUTION_CONTINUED_AS_NEW = "WORKFLOW_EXECUTION_CONTINUED_AS_NEW"
    WORKFLOW_EXECUTION_TIMED_OUT = "WORKFLOW_EXECUTION_TIMED_OUT"

    ACTIVITY_TASK_SCHEDULED = "ACTIVITY_TASK_SCHEDULED"
    ACTIVITY_TASK_STARTED = "ACTIVITY_TASK_STARTED"
    ACTIVITY_TASK_COMPLETED = "ACTIVITY_TASK_COMPLETED"
    ACTIVITY_TASK_FAILED = "ACTIVITY_TASK_FAILED"
    ACTIVITY_TASK_TIMED_OUT = "ACTIVITY_TASK_TIMED_OUT"

    CHILD_WORKFLOW_EXECUTION_STARTED = "CHILD_WORKFLOW_EXECUTION_STARTED"
    CHILD_WORKFLOW_EXECUTION_COMPLETED = "CHILD_WORKFLOW_EXECUTION_COMPLETED"
    CHILD_WORKFLOW_EXECUTION_FAILED = "CHILD_WORKFLOW_EXECUTION_FAILED"
    START_CHILD_WORKFLOW_EXECUTION_INITIATED = (
        "START_CHILD_WORKFLOW_EXECUTION_INITIATED"
    )


class WorkflowExecutionResponse(BaseModel):
    id: str = Field(..., description="The ID of the workflow execution")
    run_id: str = Field(..., description="The run ID of the workflow execution")
    start_time: datetime = Field(
        ..., description="The start time of the workflow execution"
    )
    execution_time: datetime | None = Field(
        None, description="When this workflow run started or should start."
    )
    close_time: datetime | None = Field(
        None, description="When the workflow was closed if closed."
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


EventInput = TypeVar(
    "EventInput",
    RunActionInput,
    DSLRunArgs,
    GetWorkflowDefinitionActivityInputs,
)

IGNORED_UTILITY_ACTIONS = {
    "get_schedule_activity",
    "validate_trigger_inputs_activity",
    "validate_action_activity",
}


class EventGroup(BaseModel, Generic[EventInput]):
    event_id: int
    udf_namespace: str
    udf_name: str
    udf_key: str
    action_id: str | None
    action_ref: str
    action_title: str
    action_description: str
    action_input: EventInput
    action_result: Any | None = None
    current_attempt: int | None = None
    retry_policy: ActionRetryPolicy = Field(default_factory=ActionRetryPolicy)
    start_delay: float = 0.0
    join_strategy: JoinStrategy = JoinStrategy.ALL
    related_wf_exec_id: WorkflowExecutionID | None = None

    @staticmethod
    def from_scheduled_activity(
        event: temporalio.api.history.v1.HistoryEvent,
    ) -> EventGroup[EventInput] | None:
        if (
            event.event_type
            != temporalio.api.enums.v1.EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        ):
            raise ValueError("Event is not an activity task scheduled event.")
        # Load the input data
        attrs = event.activity_task_scheduled_event_attributes
        activity_input_data = orjson.loads(attrs.input.payloads[0].data)
        # Retry policy

        act_type = attrs.activity_type.name
        if act_type in IGNORED_UTILITY_ACTIONS:
            return None
        if act_type == "get_workflow_definition_activity":
            action_input = GetWorkflowDefinitionActivityInputs(**activity_input_data)
        else:
            action_input = RunActionInput(**activity_input_data)
        if action_input.task is None:
            # It's a utility action.
            return None
        # Create an event group
        task = action_input.task
        action_retry_policy = task.retry_policy

        namespace, task_name = destructure_slugified_namespace(
            task.action, delimiter="."
        )
        return EventGroup(
            event_id=event.event_id,
            udf_namespace=namespace,
            udf_name=task_name,
            udf_key=task.action,
            action_id=task.id,
            action_ref=task.ref,
            action_title=task.title,
            action_description=task.description,
            action_input=action_input,
            retry_policy=action_retry_policy,
            start_delay=task.start_delay,
            join_strategy=task.join_strategy,
        )

    @staticmethod
    def from_initiated_child_workflow(
        event: temporalio.api.history.v1.HistoryEvent,
    ) -> EventGroup[DSLRunArgs]:
        if (
            event.event_type
            != temporalio.api.enums.v1.EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED
        ):
            raise ValueError("Event is not a child workflow initiated event.")

        wf_exec_id = cast(
            WorkflowExecutionID,
            event.start_child_workflow_execution_initiated_event_attributes.workflow_id,
        )
        # Load the input data
        input = orjson.loads(
            event.start_child_workflow_execution_initiated_event_attributes.input.payloads[
                0
            ].data
        )
        dsl_run_args = DSLRunArgs(**input)
        # Create an event group
        return EventGroup(
            event_id=event.event_id,
            udf_namespace="core.workflow",
            udf_name="execute",
            udf_key="core.workflow.execute",
            action_id=dsl_run_args.wf_id,
            action_ref=dsl_run_args.dsl.title,
            action_title=dsl_run_args.dsl.title,
            action_description=dsl_run_args.dsl.description,
            action_input=dsl_run_args,
            related_wf_exec_id=wf_exec_id,
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
        elif (
            event.event_type
            == temporalio.api.enums.v1.EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_FAILED
        ):
            failure = event.child_workflow_execution_failed_event_attributes.failure
        else:
            raise ValueError("Event type not supported for failure extraction.")

        return EventFailure(
            message=failure.message,
            stack_trace=failure.stack_trace,
            cause=MessageToDict(failure.cause) if failure.cause is not None else None,
            application_failure_info=MessageToDict(failure.application_failure_info),
        )


class EventHistoryResponse(BaseModel, Generic[EventInput]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    event_id: int
    event_time: datetime
    event_type: EventHistoryType
    task_id: int
    event_group: EventGroup[EventInput] | None = Field(
        default=None,
        description="The action group of the event. We use this to keep track of what events are related to each other.",
    )
    failure: EventFailure | None = None
    result: Any | None = None
    role: Role | None = None
    parent_wf_exec_id: WorkflowExecutionID | None = None
    workflow_timeout: float | None = None


class CreateWorkflowExecutionParams(BaseModel):
    workflow_id: WorkflowID
    inputs: TriggerInputs | None = None


class CreateWorkflowExecutionResponse(TypedDict):
    message: str
    wf_id: WorkflowID
    wf_exec_id: WorkflowExecutionID


class DispatchWorkflowResult(TypedDict):
    wf_id: WorkflowID
    result: Any


class TerminateWorkflowExecutionParams(BaseModel):
    reason: str | None = None
