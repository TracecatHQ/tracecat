from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import (
    Annotated,
    Any,
    Literal,
    NotRequired,
    TypedDict,
    cast,
)

import temporalio.api.enums.v1
import temporalio.api.history.v1
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator
from temporalio.client import WorkflowExecution, WorkflowExecutionStatus
from tracecat_ee.agent.types import AgentWorkflowID
from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs

from tracecat.auth.types import Role
from tracecat.dsl.action import ScatterActionInput
from tracecat.dsl.common import (
    AgentActionMemo,
    ChildWorkflowMemo,
    DSLRunArgs,
    get_execution_type_from_search_attr,
    get_trigger_type_from_search_attr,
)
from tracecat.dsl.enums import JoinStrategy, PlatformAction, WaitStrategy
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionRetryPolicy,
    RunActionInput,
    StreamID,
    TriggerInputs,
)
from tracecat.ee.interactions.schemas import (
    InteractionInput,
    InteractionRead,
    InteractionResult,
)
from tracecat.identifiers import WorkflowExecutionID, WorkflowID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowUUID
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock
from tracecat.sessions import Session
from tracecat.storage.object import CollectionObject, StoredObject
from tracecat.workflow.executions.common import (
    HISTORY_TO_WF_EVENT_TYPE,
    extract_first,
    is_action_activity,
)
from tracecat.workflow.executions.enums import (
    ExecutionType,
    TriggerType,
    WorkflowEventType,
    WorkflowExecutionEventStatus,
)
from tracecat.workflow.management.schemas import GetWorkflowDefinitionActivityInputs

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

_ERROR_MESSAGE_MAX_LENGTH = 2048
_SENSITIVE_ERROR_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+"),
        r"\1 [REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret)=([^&\s]+)"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(r"(?i)(authorization:\s*(?:basic|bearer)\s+)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)(://[^/\s:@]+:)([^@\s/]+)@"),
        r"\1[REDACTED]@",
    ),
)


class WorkflowExecutionBase(BaseModel):
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
    parent_wf_exec_id: WorkflowExecutionID | None = None
    trigger_type: TriggerType
    execution_type: ExecutionType = Field(
        default=ExecutionType.PUBLISHED,
        description="Execution type (draft or published). Draft uses the draft workflow graph.",
    )


class WorkflowExecutionReadMinimal(WorkflowExecutionBase):
    @staticmethod
    def from_dataclass(execution: WorkflowExecution) -> WorkflowExecutionReadMinimal:
        return WorkflowExecutionReadMinimal(
            id=execution.id,
            run_id=execution.run_id,
            start_time=execution.start_time,
            execution_time=execution.execution_time,
            close_time=execution.close_time,
            status=execution.status,
            workflow_type=execution.workflow_type,
            task_queue=execution.task_queue,
            history_length=execution.history_length,
            parent_wf_exec_id=execution.parent_id,
            trigger_type=get_trigger_type_from_search_attr(
                execution.typed_search_attributes, execution.id
            ),
            execution_type=get_execution_type_from_search_attr(
                execution.typed_search_attributes
            ),
        )


class WorkflowExecutionRead(WorkflowExecutionBase):
    events: list[WorkflowExecutionEvent] = Field(
        ..., description="The events in the workflow execution"
    )
    interactions: list[InteractionRead] = Field(
        default_factory=list,
        description="The interactions in the workflow execution",
    )


class WorkflowExecutionReadCompact[TInput: Any, TResult: Any, TSessionEvent: Any](
    WorkflowExecutionBase
):
    events: list[WorkflowExecutionEventCompact[TInput, TResult, TSessionEvent]] = Field(
        ..., description="Compact events in the workflow execution"
    )
    interactions: list[InteractionRead] = Field(
        default_factory=list,
        description="The interactions in the workflow execution",
    )
    registry_lock: RegistryLock | None = Field(
        default=None,
        description=(
            "Registry lock used for this run. For draft executions this is "
            "resolved at workflow start; for published executions this comes "
            "from the committed definition/start arguments."
        ),
    )


class WorkflowExecutionObjectField(StrEnum):
    ACTION_RESULT = "action_result"


class WorkflowExecutionObjectRequest(BaseModel):
    event_id: int = Field(..., ge=1, description="Temporal history event ID")
    field: WorkflowExecutionObjectField = Field(
        default=WorkflowExecutionObjectField.ACTION_RESULT
    )
    collection_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional index into a CollectionObject result. "
            "When omitted, operates on the top-level object result."
        ),
    )


class WorkflowExecutionCollectionPageRequest(BaseModel):
    event_id: int = Field(..., ge=1, description="Temporal history event ID")
    field: WorkflowExecutionObjectField = Field(
        default=WorkflowExecutionObjectField.ACTION_RESULT
    )
    offset: int = Field(default=0, ge=0, description="Page start index (0-indexed)")
    limit: int = Field(
        default=25,
        ge=1,
        le=100,
        description="Maximum number of items to return",
    )


class WorkflowExecutionCollectionPageItemKind(StrEnum):
    STORED_OBJECT_REF = "stored_object_ref"
    INLINE_VALUE = "inline_value"


class WorkflowExecutionCollectionPageItem(BaseModel):
    index: int = Field(..., ge=0, description="Collection index for this item")
    kind: WorkflowExecutionCollectionPageItemKind = Field(
        ..., description="Descriptor type for this collection page item"
    )
    stored: StoredObject | None = Field(
        default=None,
        description="StoredObject descriptor when kind is stored_object_ref",
    )
    value_preview: str | None = Field(
        default=None,
        description="UTF-8 preview of serialized inline value when kind is inline_value",
    )
    value_size_bytes: int | None = Field(
        default=None,
        ge=0,
        description="Serialized inline value size in bytes when kind is inline_value",
    )
    truncated: bool = Field(
        default=False,
        description="Whether value_preview was truncated",
    )

    @model_validator(mode="after")
    def validate_kind_fields(self) -> WorkflowExecutionCollectionPageItem:
        if self.kind == WorkflowExecutionCollectionPageItemKind.STORED_OBJECT_REF:
            if self.stored is None:
                raise ValueError(
                    "`stored` is required when kind is `stored_object_ref`"
                )
            if self.value_preview is not None or self.value_size_bytes is not None:
                raise ValueError(
                    "`value_preview` and `value_size_bytes` must be omitted when kind is `stored_object_ref`"
                )
            return self

        if self.stored is not None:
            raise ValueError("`stored` must be omitted when kind is `inline_value`")
        if self.value_preview is None or self.value_size_bytes is None:
            raise ValueError(
                "`value_preview` and `value_size_bytes` are required when kind is `inline_value`"
            )
        return self


class WorkflowExecutionCollectionPageResponse(BaseModel):
    collection: CollectionObject = Field(
        ..., description="Collection metadata for the requested result"
    )
    offset: int = Field(..., ge=0, description="Requested page offset")
    limit: int = Field(..., ge=1, description="Requested page size")
    next_offset: int | None = Field(
        default=None,
        ge=0,
        description="Offset to use for next page, or null if no more items",
    )
    items: list[WorkflowExecutionCollectionPageItem] = Field(
        default_factory=list,
        description="Collection page descriptors",
    )


class WorkflowExecutionObjectDownloadResponse(BaseModel):
    download_url: str = Field(..., description="Pre-signed download URL")
    file_name: str = Field(..., description="Suggested file name")
    content_type: str = Field(..., description="MIME type of the object")
    size_bytes: int = Field(..., ge=0, description="Object size in bytes")
    expires_in_seconds: int = Field(
        ..., ge=1, description="Presigned URL expiry in seconds"
    )


class WorkflowExecutionObjectPreviewResponse(BaseModel):
    content: str = Field(..., description="Preview text content")
    content_type: str = Field(..., description="MIME type of the object")
    size_bytes: int = Field(..., ge=0, description="Total object size in bytes")
    preview_bytes: int = Field(
        ..., ge=0, description="Number of bytes used for preview"
    )
    truncated: bool = Field(
        ..., description="Whether preview is truncated due to size limits"
    )
    encoding: Literal["utf-8", "unknown"] = Field(
        ..., description="Encoding used to decode preview text"
    )


def destructure_slugified_namespace(s: str, delimiter: str = "__") -> tuple[str, str]:
    *stem, leaf = s.split(delimiter)
    return (".".join(stem), leaf)


EventInput = (
    RunActionInput
    | DSLRunArgs
    | GetWorkflowDefinitionActivityInputs
    | InteractionResult
    | InteractionInput
    | AgentWorkflowArgs
)


class EventGroup[T: EventInput](BaseModel):
    event_id: int
    udf_namespace: str
    udf_name: str
    udf_key: str
    action_id: str | None = None
    action_ref: str | None = None
    action_title: str | None = None
    action_description: str | None = None
    action_input: T
    action_result: Any | None = None
    current_attempt: int | None = None
    retry_policy: ActionRetryPolicy = Field(default_factory=ActionRetryPolicy)
    start_delay: float = 0.0
    join_strategy: JoinStrategy = JoinStrategy.ALL
    related_wf_exec_id: WorkflowExecutionID | AgentWorkflowID | None = None

    @staticmethod
    async def from_scheduled_activity(
        event: temporalio.api.history.v1.HistoryEvent,
    ) -> EventGroup[EventInput] | None:
        if (
            event.event_type
            != temporalio.api.enums.v1.EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        ):
            raise ValueError("Event is not an activity task scheduled event.")
        # Load the input data
        attrs = event.activity_task_scheduled_event_attributes
        activity_input_data = await extract_first(attrs.input)

        act_type = attrs.activity_type.name
        # Handle specific activity types we care about
        if act_type == "get_workflow_definition_activity":
            action_input = GetWorkflowDefinitionActivityInputs(**activity_input_data)
        elif is_action_activity(act_type):
            try:
                action_input = RunActionInput(**activity_input_data)
            except Exception as e:
                logger.warning("Error parsing run action input", error=e)
                return None
        else:
            # Skip all other activities (utility, internal, etc.)
            return None
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
            action_id=str(task.id) if task.id else None,
            action_ref=task.ref,
            action_title=task.title,
            action_description=task.description,
            action_input=cast(EventInput, action_input),
            retry_policy=action_retry_policy,
            start_delay=task.start_delay,
            join_strategy=task.join_strategy,
        )

    @staticmethod
    async def from_initiated_child_workflow(
        event: temporalio.api.history.v1.HistoryEvent,
    ) -> EventGroup[DSLRunArgs | AgentWorkflowArgs]:
        if (
            event.event_type
            != temporalio.api.enums.v1.EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED
        ):
            raise ValueError("Event is not a child workflow initiated event.")

        attrs = event.start_child_workflow_execution_initiated_event_attributes
        logger.debug("Child workflow initiated event", attrs=attrs.workflow_type)
        match attrs.workflow_type.name:
            case "DSLWorkflow":
                wf_exec_id: WorkflowExecutionID = attrs.workflow_id
                input = await extract_first(attrs.input)
                dsl_run_args = DSLRunArgs(**input)
                # Create an event group

                if dsl := dsl_run_args.dsl:
                    action_title = dsl.title
                    action_description = dsl.description
                else:
                    action_title = None
                    action_description = None

                wf_id = WorkflowUUID.new(dsl_run_args.wf_id)
                return EventGroup(
                    event_id=event.event_id,
                    udf_namespace="core.workflow",
                    udf_name="execute",
                    udf_key="core.workflow.execute",
                    action_id=wf_id.short(),
                    action_ref=None,
                    action_title=action_title,
                    action_description=action_description,
                    action_input=dsl_run_args,
                    related_wf_exec_id=wf_exec_id,
                )
            case "DurableAgentWorkflow":
                agent_wf_id = AgentWorkflowID.from_workflow_id(attrs.workflow_id)
                input = await extract_first(attrs.input)
                agent_run_args = AgentWorkflowArgs(**input)
                namespace, name = PlatformAction.AI_AGENT.value.split(".", 1)
                return EventGroup(
                    event_id=event.event_id,
                    udf_namespace=namespace,
                    udf_name=name,
                    udf_key=PlatformAction.AI_AGENT.value,
                    action_id=agent_wf_id,
                    action_ref=None,
                    action_title="AI Agent",
                    action_description="AI Agent",
                    action_input=agent_run_args,
                    related_wf_exec_id=agent_wf_id,
                )
            case _:
                raise ValueError("Event is not a child workflow initiated event.")

    @staticmethod
    async def from_accepted_workflow_update(
        event: temporalio.api.history.v1.HistoryEvent,
    ) -> EventGroup[InteractionInput]:
        if (
            event.event_type
            != temporalio.api.enums.v1.EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED
            or not event.HasField("workflow_execution_update_accepted_event_attributes")
        ):
            raise ValueError("Event is not a workflow update accepted event.")

        attrs = event.workflow_execution_update_accepted_event_attributes
        input = await extract_first(attrs.accepted_request.input.args)
        group = EventGroup(
            event_id=event.event_id,
            udf_namespace="core.interact",
            udf_name="response",
            udf_key="core.interact.response",
            action_input=InteractionInput(**input),
        )
        logger.debug(
            "Workflow update accepted event", event_id=event.event_id, group=group
        )
        return group


class EventFailure(BaseModel):
    message: str
    cause: dict[str, Any] | None = None
    root_cause_message: str | None = None

    @staticmethod
    def extract_root_cause_message(cause: dict[str, Any] | None) -> str | None:
        """Extract the deepest non-empty message from nested Temporal failure causes."""
        if not cause:
            return None

        root_message: str | None = None
        current: dict[str, Any] | None = cause
        seen: set[int] = set()
        # Termination argument:
        # - Each non-breaking iteration adds a new object id to `seen`.
        # - If a dict repeats (cycle), we break on the `seen` check.
        # - If `cause` is missing or not a dict, we break.
        # Therefore the loop cannot run indefinitely.
        while current is not None:
            current_id = id(current)
            if current_id in seen:
                break
            seen.add(current_id)

            message = current.get("message")
            if isinstance(message, str) and message.strip():
                root_message = message

            nested_cause = current.get("cause")
            if not isinstance(nested_cause, dict):
                break
            current = nested_cause

        return root_message

    @staticmethod
    def sanitize_error_text(text: str | None) -> str | None:
        if text is None:
            return None

        sanitized = text
        for pattern, replacement in _SENSITIVE_ERROR_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        if len(sanitized) > _ERROR_MESSAGE_MAX_LENGTH:
            return f"{sanitized[:_ERROR_MESSAGE_MAX_LENGTH]}...[truncated]"
        return sanitized

    @staticmethod
    def from_history_event(
        event: temporalio.api.history.v1.HistoryEvent,
        *,
        include_raw_cause: bool = False,
    ) -> EventFailure:
        match event.event_type:
            case temporalio.api.enums.v1.EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED:
                failure = event.activity_task_failed_event_attributes.failure
            case temporalio.api.enums.v1.EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED:
                failure = event.workflow_execution_failed_event_attributes.failure
            case temporalio.api.enums.v1.EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_FAILED:
                failure = event.child_workflow_execution_failed_event_attributes.failure
            case temporalio.api.enums.v1.EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_COMPLETED:
                failure = event.workflow_execution_update_completed_event_attributes.outcome.failure
            case _:
                raise ValueError("Event type not supported for failure extraction.")

        cause = MessageToDict(failure.cause) if failure.HasField("cause") else None
        root_cause_message = EventFailure.extract_root_cause_message(cause)
        return EventFailure(
            message=EventFailure.sanitize_error_text(failure.message) or "",
            cause=cause if include_raw_cause else None,
            root_cause_message=EventFailure.sanitize_error_text(root_cause_message),
        )


class WorkflowExecutionEvent[T: EventInput](BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    event_id: int
    event_time: datetime
    event_type: WorkflowEventType
    task_id: int
    event_group: EventGroup[T] | None = Field(
        default=None,
        description="The action group of the event. We use this to keep track of what events are related to each other.",
    )
    failure: EventFailure | None = None
    result: Any | None = None
    role: Role | None = None
    parent_wf_exec_id: WorkflowExecutionID | None = None
    workflow_timeout: float | None = None


class WorkflowExecutionEventCompact[TInput: Any, TResult: Any, TSessionEvent: Any](
    BaseModel
):
    """A compact representation of a workflow execution event."""

    source_event_id: int
    """The event ID of the source event."""
    schedule_time: datetime
    start_time: datetime | None = None
    close_time: datetime | None = None
    curr_event_type: WorkflowEventType
    """The type of the event."""
    status: WorkflowExecutionEventStatus
    action_name: str
    action_ref: str
    action_input: TInput | None = None
    action_result: TResult | None = None
    action_error: EventFailure | None = None
    stream_id: StreamID = ROOT_STREAM
    """The execution stream ID of the event, not to be confused with SSE streaming."""
    child_wf_exec_id: WorkflowExecutionID | None = None
    child_wf_count: int = 0
    loop_index: int | None = None
    child_wf_wait_strategy: WaitStrategy | None = None
    # SSE streaming for agents
    session: Session[TSessionEvent] | None = None

    @staticmethod
    async def from_source_event(
        event: temporalio.api.history.v1.HistoryEvent,
    ) -> WorkflowExecutionEventCompact | None:
        match event.event_type:
            case temporalio.api.enums.v1.EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
                return await WorkflowExecutionEventCompact.from_scheduled_activity(
                    event
                )
            case temporalio.api.enums.v1.EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED:
                return (
                    await WorkflowExecutionEventCompact.from_initiated_child_workflow(
                        event
                    )
                )
            case temporalio.api.enums.v1.EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED:
                return (
                    await WorkflowExecutionEventCompact.from_workflow_update_accepted(
                        event
                    )
                )
            case _:
                return None

    @staticmethod
    async def from_scheduled_activity(
        event: temporalio.api.history.v1.HistoryEvent,
    ) -> WorkflowExecutionEventCompact | None:
        if (
            event.event_type
            != temporalio.api.enums.v1.EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        ):
            raise ValueError("Event is not an activity task scheduled event.")
        attrs = event.activity_task_scheduled_event_attributes
        activity_input_data = await extract_first(attrs.input)

        act_type = attrs.activity_type.name
        # Only parse activities that use action schemas
        if not is_action_activity(act_type):
            logger.trace("Skipping non-action activity", act_type=act_type)
            return None

        # Handle ScatterActionInput for scatter activities
        if act_type == "handle_scatter_input_activity":
            try:
                scatter_input = ScatterActionInput(**activity_input_data)
            except Exception as e:
                logger.warning("Error parsing scatter action input", error=e)
                return None
            task = scatter_input.task
            if task is None:
                logger.debug("Scatter input task is None", event_id=event.event_id)
                return None

            return WorkflowExecutionEventCompact(
                source_event_id=event.event_id,
                schedule_time=event.event_time.ToDatetime(UTC),
                curr_event_type=HISTORY_TO_WF_EVENT_TYPE[event.event_type],
                status=WorkflowExecutionEventStatus.SCHEDULED,
                action_name=task.action,
                action_ref=task.ref,
                action_input=task.args,
                stream_id=scatter_input.stream_id or ROOT_STREAM,
                session=None,
            )

        # Handle RunActionInput for other action activities
        try:
            action_input = RunActionInput(**activity_input_data)
        except Exception as e:
            logger.warning("Error parsing run action input", error=e)
            return None
        task = action_input.task
        if task is None:
            logger.debug("Action input is None", event_id=event.event_id)
            return None

        session = None
        if action_input.session_id is not None:
            session = Session(id=action_input.session_id)  # No events

        return WorkflowExecutionEventCompact(
            source_event_id=event.event_id,
            schedule_time=event.event_time.ToDatetime(UTC),
            curr_event_type=HISTORY_TO_WF_EVENT_TYPE[event.event_type],
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name=task.action,
            action_ref=task.ref,
            action_input=task.args,
            stream_id=action_input.stream_id,
            session=session,
        )

    @staticmethod
    async def from_initiated_child_workflow(
        event: temporalio.api.history.v1.HistoryEvent,
    ) -> WorkflowExecutionEventCompact | None:
        """Creates a compact workflow execution event from a child workflow initiation event.

        Args:
            event: The temporal history event representing a child workflow initiation

        Returns:
            WorkflowExecutionEventCompact | None: The compact event representation, or None if invalid
        """
        if (
            event.event_type
            != temporalio.api.enums.v1.EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED
        ):
            raise ValueError("Event is not a child workflow initiated event.")

        attrs = event.start_child_workflow_execution_initiated_event_attributes
        wf_exec_id: WorkflowExecutionID = attrs.workflow_id
        match attrs.workflow_type.name:
            case "DSLWorkflow":
                try:
                    memo = ChildWorkflowMemo.from_temporal(attrs.memo)
                except Exception as e:
                    logger.error("Error parsing child workflow memo", error=e)
                    raise e

                if (
                    attrs.parent_close_policy
                    == temporalio.api.enums.v1.ParentClosePolicy.PARENT_CLOSE_POLICY_ABANDON
                    and memo.wait_strategy == WaitStrategy.DETACH
                ):
                    status = WorkflowExecutionEventStatus.DETACHED
                else:
                    status = WorkflowExecutionEventStatus.SCHEDULED
                logger.debug(
                    "Child workflow initiated event",
                    status=status,
                    wf_exec_id=wf_exec_id,
                    memo=memo,
                )

                input_data = await extract_first(attrs.input)
                dsl_run_args = DSLRunArgs(**input_data)

                return WorkflowExecutionEventCompact(
                    source_event_id=event.event_id,
                    schedule_time=event.event_time.ToDatetime(UTC),
                    curr_event_type=HISTORY_TO_WF_EVENT_TYPE[event.event_type],
                    status=status,
                    action_name=PlatformAction.CHILD_WORKFLOW_EXECUTE.value,
                    action_ref=memo.action_ref,
                    action_input=dsl_run_args.trigger_inputs,
                    child_wf_exec_id=wf_exec_id,
                    loop_index=memo.loop_index,
                    child_wf_wait_strategy=memo.wait_strategy,
                    stream_id=memo.stream_id,
                )
            case "DurableAgentWorkflow":
                try:
                    memo = AgentActionMemo.from_temporal(attrs.memo)
                except Exception as e:
                    logger.error("Error parsing agent action memo", error=e)
                    raise e

                input_data = await extract_first(attrs.input)
                agent_run_args = AgentWorkflowArgs(**input_data)
                session = None
                session_id = agent_run_args.agent_args.session_id
                if session_id is not None:
                    session = Session(id=session_id)
                return WorkflowExecutionEventCompact(
                    source_event_id=event.event_id,
                    schedule_time=event.event_time.ToDatetime(UTC),
                    curr_event_type=HISTORY_TO_WF_EVENT_TYPE[event.event_type],
                    status=WorkflowExecutionEventStatus.SCHEDULED,
                    action_name=PlatformAction.AI_AGENT.value,
                    action_ref=memo.action_ref,
                    action_input=agent_run_args,
                    child_wf_exec_id=None,
                    loop_index=memo.loop_index,
                    stream_id=memo.stream_id,
                    session=session,
                )
            case _:
                raise ValueError(
                    f"Unexpected child workflow type: {attrs.workflow_type.name}"
                )

    @staticmethod
    async def from_workflow_update_accepted(
        event: temporalio.api.history.v1.HistoryEvent,
    ) -> WorkflowExecutionEventCompact | None:
        if (
            event.event_type
            != temporalio.api.enums.v1.EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED
        ):
            raise ValueError("Event is not a workflow update accepted event.")

        attrs = event.workflow_execution_update_accepted_event_attributes
        input_data = await extract_first(attrs.accepted_request.input.args)
        signal_input = InteractionInput(**input_data)
        return WorkflowExecutionEventCompact(
            source_event_id=event.event_id,
            schedule_time=event.event_time.ToDatetime(UTC),
            curr_event_type=HISTORY_TO_WF_EVENT_TYPE[event.event_type],
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name=signal_input.action_ref,
            action_ref=signal_input.action_ref,
            action_input=signal_input,
        )


class WorkflowExecutionCreate(BaseModel):
    workflow_id: AnyWorkflowID
    inputs: TriggerInputs | None = None
    time_anchor: datetime | None = Field(
        default=None,
        description=(
            "Override the workflow's time anchor for FN.now() and related functions. "
            "If not provided, computed from TemporalScheduledStartTime (for schedules) "
            "or workflow start_time (for other triggers)."
        ),
    )


class WorkflowExecutionCreateResponse(TypedDict):
    message: str
    wf_id: WorkflowID
    wf_exec_id: WorkflowExecutionID
    payload: NotRequired[Any]
    """The HTTP request body of the request that triggered the workflow."""


class WorkflowDispatchResponse(TypedDict):
    wf_id: WorkflowID
    result: Any


class WorkflowExecutionTerminate(BaseModel):
    reason: str | None = None


class ReceiveInteractionResponse(BaseModel):
    message: str
