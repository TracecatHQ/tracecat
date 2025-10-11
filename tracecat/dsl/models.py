from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, ClassVar, Literal, NotRequired, Required, Self, TypedDict

from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic_core import CoreSchema, core_schema

from tracecat.dsl.constants import DEFAULT_ACTION_TIMEOUT
from tracecat.dsl.enums import JoinStrategy, StreamErrorHandlingStrategy
from tracecat.expressions.common import ExprContext
from tracecat.expressions.validation import ExpressionStr, RequiredExpressionStr
from tracecat.identifiers import WorkflowExecutionID, WorkflowRunID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowUUID
from tracecat.interactions.models import ActionInteraction, InteractionContext
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.types.exceptions import TracecatValidationError

SLUG_PATTERN = r"^[a-z0-9_]+$"
ACTION_TYPE_PATTERN = r"^[a-z0-9_.]+$"

TriggerInputs = Any
"""Trigger inputs JSON type."""

ExecutionContext = dict[ExprContext, Any]
"""Workflow execution context."""


class TaskResult[TResult: Any, TError: Any](TypedDict):
    """Result of executing a DSL node."""

    result: Required[TResult]
    result_typename: Required[str]
    error: NotRequired[TError]
    error_typename: NotRequired[str]
    interaction: NotRequired[Any]
    interaction_id: NotRequired[str]
    interaction_type: NotRequired[str]


@dataclass(frozen=True)
class ActionErrorInfo:
    """Contains information about an action error."""

    ref: str
    """The task reference."""

    message: str
    """The error message."""

    type: str
    """The error type."""

    expr_context: ExprContext = ExprContext.ACTIONS
    """The expression context where the error occurred."""

    attempt: int = 1
    """The attempt number."""

    def format(self, loc: str = "run_action") -> str:
        locator = f"{self.expr_context}.{self.ref} -> {loc}"
        return f"[{locator}] (Attempt {self.attempt})\n\n{self.message}"


ActionErrorInfoAdapter = TypeAdapter(ActionErrorInfo)


class ActionRetryPolicy(BaseModel):
    max_attempts: int = Field(
        default=1,
        description="Total number of execution attempts. 0 means unlimited, 1 means no retries.",
    )
    timeout: int = Field(
        default=DEFAULT_ACTION_TIMEOUT, description="Timeout for the action in seconds."
    )
    retry_until: RequiredExpressionStr | None = Field(
        default=None, description="Retry until a specific condition is met."
    )


class ActionStatement(BaseModel):
    id: str | None = Field(
        default=None,
        exclude=True,
        description=(
            "The action ID. If this is populated means there is a corresponding action"
            "in the database `Action` table."
        ),
    )

    ref: str = Field(pattern=SLUG_PATTERN, description="Unique reference for the task")

    description: str = ""

    action: str = Field(
        pattern=ACTION_TYPE_PATTERN,
        description="Action type. Equivalent to the UDF key.",
    )
    """Action type. Equivalent to the UDF key."""

    args: Mapping[str, Any] = Field(
        default_factory=dict, description="Arguments for the action"
    )

    depends_on: list[str] = Field(default_factory=list, description="Task dependencies")

    interaction: ActionInteraction | None = Field(
        default=None,
        description="Whether the action is interactive.",
    )

    """Control flow options"""

    run_if: ExpressionStr | None = Field(
        default=None, description="Condition to run the task"
    )
    for_each: ExpressionStr | list[ExpressionStr] | None = Field(
        default=None,
        description="Iterate over a list of items and run the task for each item.",
    )
    retry_policy: ActionRetryPolicy = Field(
        default_factory=ActionRetryPolicy, description="Retry policy for the action."
    )
    start_delay: float = Field(
        default=0.0,
        description=(
            "Delay before starting the action in seconds. "
            "If `wait_until` is also provided, the `wait_until` timer will take precedence."
        ),
    )
    wait_until: str | None = Field(
        default=None,
        description=(
            "Wait until a specific date and time before starting. Overrides `start_delay` if both are provided."
        ),
    )
    join_strategy: JoinStrategy = Field(
        default=JoinStrategy.ALL,
        description=(
            "The strategy to use when joining on this task. "
            "By default, all branches must complete successfully before the join task can complete."
        ),
    )
    environment: ExpressionStr | None = Field(
        default=None,
        description="Override environment for this action's execution. Can be a template expression.",
    )

    @property
    def title(self) -> str:
        return self.ref.capitalize().replace("_", " ")

    @model_validator(mode="after")
    def validate_interaction(self):
        if self.interaction and self.for_each:
            raise TracecatValidationError(
                "Interaction is not allowed when for_each is provided."
            )
        return self


class DSLConfig(BaseModel):
    """This is the runtime configuration for the workflow.

    Activities don't need access to this.
    """

    scheduler: Literal["static", "dynamic"] = Field(
        default="dynamic",
        description="The type of scheduler to use.",
        exclude=True,  # Exclude from serialization
    )
    environment: ExpressionStr = Field(
        default=DEFAULT_SECRETS_ENVIRONMENT,
        description=(
            "The workflow's target execution environment. "
            "This is used to isolate secrets across different environments."
            "If not provided, the default environment (default) is used."
        ),
    )
    timeout: float = Field(
        default=0,
        description="Workflow timeout in seconds. If set to 0, the workflow has no timeout.",
    )
    """Workflow timeout in seconds. If set to 0, the workflow has no timeout."""


class Trigger(BaseModel):
    type: Literal["schedule", "webhook"]
    ref: str = Field(pattern=SLUG_PATTERN)
    args: dict[str, Any] = Field(default_factory=dict)


class DSLEnvironment(TypedDict, total=False):
    """DSL Environment context. Has metadata about the workflow."""

    workflow: dict[str, Any]
    """Metadata about the workflow."""

    environment: str
    """Target environment for the workflow."""

    variables: dict[str, Any]
    """Environment variables."""

    registry_version: str
    """The registry version to use for the workflow."""


class RunContext(BaseModel):
    """This is the runtime context model for a workflow run. Passed into activities."""

    wf_id: WorkflowUUID
    wf_exec_id: WorkflowExecutionID
    wf_run_id: WorkflowRunID
    environment: str

    @field_validator("wf_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, v: AnyWorkflowID) -> WorkflowUUID:
        """Convert any valid workflow ID format to WorkflowUUID."""
        return WorkflowUUID.new(v)


type SkipToken = Literal["skip"]


class StreamID(str):
    """Hierarchical stream identifier: 'scatter_1:2/scatter_2:0'"""

    __stream_sep: ClassVar[str] = "/"
    __idx_sep: ClassVar[str] = ":"

    @classmethod
    def new(
        cls, scope: str, index: int | SkipToken, *, base_stream_id: Self | None = None
    ) -> Self:
        """Create a stream ID for an inherited scatter item.

        Args:
            scope: The scope name for the stream.
            index: The index or skip token for the stream.
            base_stream_id: The base stream ID to append to, if any.

        Returns:
            Self: A new StreamID instance.
        """
        new_stream = cls(f"{scope}{cls.__idx_sep}{index}")
        if base_stream_id is None:
            return new_stream
        return cls(f"{base_stream_id}{cls.__stream_sep}{new_stream}")

    @classmethod
    def skip(cls, scope: str, *, base_stream_id: Self | None = None) -> Self:
        """Create a stream ID for a skipped scatter.

        Args:
            scope: The scope name for the stream.
            base_stream_id: The base stream ID to append to, if any.

        Returns:
            Self: A new StreamID instance representing a skipped scatter.
        """
        return cls.new(scope, "skip", base_stream_id=base_stream_id)

    @cached_property
    def streams(self) -> list[str]:
        """Get the list of streams in the stream ID.

        Returns:
            list[str]: The split stream segments.
        """
        return self.split(self.__stream_sep)

    @cached_property
    def leaf(self) -> tuple[str, int | SkipToken]:
        """Get the leaf stream ID.

        Returns:
            tuple[str, int | SkipToken]: The scope and index/skip token of the leaf.

        Raises:
            ValueError: If the stream ID is invalid.
        """
        scope, index, *rest = self.streams[-1].split(self.__idx_sep)
        if rest:
            raise ValueError(f"Invalid stream ID: {self}")
        return scope, int(index) if index != "skip" else "skip"

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: object,
        _handler: object,
    ) -> CoreSchema:
        """
        Generate Pydantic core schema for validation and serialization.

        This simplified version uses only the plain validator and serializer for str,
        as StreamID is a subclass of str and does not require union or instance checks.

        Returns:
            CoreSchema: The Pydantic core schema for StreamID.
        """
        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_plain_validator_function(cls),
            python_schema=core_schema.no_info_plain_validator_function(cls),
            serialization=core_schema.plain_serializer_function_ser_schema(
                str,
                return_schema=core_schema.str_schema(),
                when_used="json",
            ),
        )


ROOT_STREAM = StreamID.new("<root>", 0)


class RunActionInput(BaseModel):
    """This object contains all the information needed to execute an action."""

    task: ActionStatement
    exec_context: ExecutionContext
    run_context: RunContext
    # This gets passed in from the worker
    interaction_context: InteractionContext | None = None
    stream_id: StreamID = ROOT_STREAM
    session_id: uuid.UUID | None = None
    """ID for a streamable session, if any."""

    @model_validator(mode="before")
    @classmethod
    def _ignore_deprecated_inputs_context(cls, data: Any):
        """Drop legacy INPUTS execution context entries."""
        if isinstance(data, dict):
            exec_ctx = data.get("exec_context")
            if isinstance(exec_ctx, dict) and "INPUTS" in exec_ctx:
                exec_ctx.pop("INPUTS", None)
        return data


class DSLExecutionError(TypedDict, total=False):
    """A proxy for an exception.

    This is the object that gets returned in place of an exception returned when
    using `asyncio.gather(..., return_exceptions=True)`, as Exception types aren't serializable."""

    is_error: bool
    """A flag to indicate that this object is an error."""

    type: str
    """The type of the exception. e.g. `ValueError`"""

    message: str
    """The message of the exception."""


@dataclass(frozen=True)
class TaskExceptionInfo:
    exception: Exception
    details: ActionErrorInfo


@dataclass(frozen=True, slots=True)
class Task:
    """Stream-aware task instance"""

    ref: str
    """The task action reference"""
    stream_id: StreamID
    """The stream ID of the task"""
    delay: float = field(default=0.0, compare=False)
    """Delay in seconds before scheduling an action."""


class ScatterArgs(BaseModel):
    collection: ExpressionStr | list[Any] = Field(
        ..., description="The collection to scatter"
    )
    interval: float | None = Field(
        default=None, description="The interval in seconds between each scatter task"
    )


class GatherArgs(BaseModel):
    """Arguments for gather operations"""

    items: ExpressionStr = Field(..., description="The jsonpath to select items from")
    drop_nulls: bool = Field(
        default=False, description="Whether to drop null values from the final result"
    )
    error_strategy: StreamErrorHandlingStrategy = Field(
        default=StreamErrorHandlingStrategy.PARTITION
    )
