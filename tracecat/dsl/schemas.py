from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from functools import cached_property
from typing import Any, ClassVar, Literal, NotRequired, Self, TypedDict

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)
from pydantic_core import CoreSchema, core_schema

from tracecat.dsl.constants import DEFAULT_ACTION_TIMEOUT
from tracecat.dsl.enums import JoinStrategy, StreamErrorHandlingStrategy
from tracecat.exceptions import TracecatValidationError
from tracecat.expressions.validation import ExpressionStr, RequiredExpressionStr
from tracecat.identifiers import WorkflowExecutionID, WorkflowRunID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowUUID
from tracecat.interactions.schemas import ActionInteraction, InteractionContext
from tracecat.registry.lock.types import RegistryLock
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.storage.object import InlineObject, StoredObject

SLUG_PATTERN = r"^[a-z0-9_]+$"
ACTION_TYPE_PATTERN = r"^[a-z0-9_.]+$"

TriggerInputs = Any
"""Trigger inputs JSON type."""


class ExecutionContext(TypedDict):
    """Workflow execution context with typed fields.

    ACTIONS and TRIGGER are always present. Other fields are optional since
    contexts may be built incrementally during workflow execution.
    """

    ACTIONS: dict[str, TaskResult]
    """Action results keyed by action ref. TaskResult.result is StoredObject."""

    TRIGGER: StoredObject | None
    """Trigger inputs wrapped in StoredObject (InlineObject or ExternalObject).
    Always present - None signals no trigger inputs were provided."""

    ENV: NotRequired[DSLEnvironment]
    """Environment metadata about the workflow."""

    SECRETS: NotRequired[dict[str, Any]]
    """Secrets context."""

    VARS: NotRequired[dict[str, Any]]
    """Workspace variables."""

    var: NotRequired[dict[str, Any]]
    """Action-local variables, used in for_each loops."""


class TemplateExecutionContext(TypedDict):
    ENV: NotRequired[DSLEnvironment]
    """Environment metadata about the workflow."""

    SECRETS: NotRequired[dict[str, Any]]
    """Secrets context."""

    VARS: NotRequired[dict[str, Any]]
    """Workspace variables."""

    inputs: dict[str, Any]
    """Template action inputs."""

    steps: dict[str, MaterializedTaskResult]
    """Template action step results (materialized for expression access)."""


class MaterializedTaskResult(TypedDict):
    """TaskResult with StoredObject materialized to raw value.

    Used after materialize_operand() retrieves data from storage.
    """

    result: Any
    """The raw result value (not wrapped in StoredObject)."""

    result_typename: str
    """The Python type name of the result."""

    error: Any | None
    """Error details if execution failed."""

    error_typename: str | None
    """The Python type name of the error if one occurred."""

    interaction: Any | None
    """Interaction metadata for interactive actions."""

    interaction_id: str | None
    """ID of the interaction if this was an interactive action."""

    interaction_type: str | None
    """Type of interaction if this was an interactive action."""


class MaterializedExecutionContext(TypedDict, total=False):
    """ExecutionContext with all StoredObjects materialized to raw values.

    Used as the output type of materialize_operand(). ACTIONS contains
    MaterializedTaskResult (raw values) instead of TaskResult (StoredObject).
    """

    ACTIONS: dict[str, MaterializedTaskResult]
    """Action results with raw values (not StoredObject)."""

    TRIGGER: Any
    """Raw trigger data (not wrapped in StoredObject)."""

    ENV: DSLEnvironment
    """Environment metadata about the workflow."""

    SECRETS: dict[str, Any]
    """Secrets context."""

    VARS: dict[str, Any]
    """Workspace variables."""

    var: dict[str, Any]
    """Action-local variables, used in for_each loops."""


class TaskResult(BaseModel):
    """Result of executing a DSL node.

    With uniform envelope design, `result` is always a StoredObject:
    - InlineObject when data is small or externalization is disabled
    - ExternalObject when data is large and externalization is enabled
    """

    result: StoredObject
    """The action result wrapped in StoredObject (InlineObject or ExternalObject)."""

    result_typename: str
    """The Python type name of the original result before wrapping."""

    error: Any | None = None
    """Error details if execution failed."""

    error_typename: str | None = None
    """The Python type name of the error if one occurred."""

    interaction: Any | None = None
    """Interaction metadata for interactive actions."""

    interaction_id: str | None = None
    """ID of the interaction if this was an interactive action."""

    interaction_type: str | None = None
    """Type of interaction if this was an interactive action."""

    collection_index: int | None = None
    """Index into a stored collection for scatter items.

    When set, `result` contains the entire collection and this index specifies
    which item from the collection this TaskResult represents. Used for
    efficient storage of scatter collections.
    """

    @classmethod
    def from_result(cls, result: Any, **kwargs: Any) -> Self:
        """Create a TaskResult from a raw result value.

        Wraps the result in InlineObject for uniform envelope.
        Use this for creating TaskResult from raw Python values.

        Args:
            result: The raw result value to wrap
            **kwargs: Additional fields (error, interaction, etc.)

        Returns:
            TaskResult with result wrapped in InlineObject
        """
        return cls(
            result=InlineObject(data=result),
            result_typename=type(result).__name__,
            **kwargs,
        )

    @classmethod
    def from_collection_item(
        cls, stored: StoredObject, index: int, item_typename: str
    ) -> Self:
        """Create a TaskResult referencing an item in a stored collection.

        Used for scatter operations where the entire collection is stored once
        and individual items are referenced by index.

        Args:
            stored: The StoredObject containing the entire collection
            index: Index of this item within the collection
            item_typename: Type name of the individual item

        Returns:
            TaskResult with collection_index set
        """
        return cls(
            result=stored,
            result_typename=item_typename,
            collection_index=index,
        )

    def is_error(self) -> bool:
        """Check if this result represents an error."""
        return self.error is not None

    def is_externalized(self) -> bool:
        """Check if the result is stored externally (in S3/MinIO)."""
        return self.result.type in ("external", "collection")

    def get_data(self) -> Any:
        """Get the raw data from the StoredObject.

        For InlineObject, returns the data directly.
        For ExternalObject, raises an error (must be materialized first).
        For collection items (collection_index set), returns the indexed item.
        """
        if self.result.type == "inline":
            data = self.result.data
            if self.collection_index is not None and isinstance(data, list):
                return data[self.collection_index]
            return data
        raise ValueError(
            "Cannot get data from ExternalObject. Use materialize_operand() first."
        )

    def with_result(self, result: Any) -> Self:
        """Create a copy with a new result value (wrapped in InlineObject).

        Use this instead of update() for Pydantic models.
        """
        return self.model_copy(
            update={
                "result": InlineObject(data=result),
                "result_typename": type(result).__name__,
            }
        )

    def with_error(self, error: Any, error_typename: str | None = None) -> Self:
        """Create a copy with error information added."""
        return self.model_copy(
            update={
                "error": error,
                "error_typename": error_typename or type(error).__name__,
            }
        )

    def update(self, *args: Any, **kwargs: Any) -> None:
        """Dict-like update helper for compatibility with legacy callers."""
        updates: dict[str, Any] = {}
        if args:
            if len(args) != 1 or not isinstance(args[0], Mapping):
                raise TypeError("update() takes at most one mapping positional arg")
            updates.update(args[0])
        updates.update(kwargs)
        for key, value in updates.items():
            setattr(self, key, value)

    def to_materialized_dict(self) -> MaterializedTaskResult:
        """Convert to a dict with unwrapped result for jsonpath access.

        Used in template execution contexts where jsonpath needs dict-like
        access to fields, including the raw result value.
        """
        return MaterializedTaskResult(
            result=self.get_data() if self.result.type == "inline" else None,
            result_typename=self.result_typename,
            error=self.error,
            error_typename=self.error_typename,
            interaction=self.interaction,
            interaction_id=self.interaction_id,
            interaction_type=self.interaction_type,
        )


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
    id: uuid.UUID | None = Field(
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
    logical_time: datetime
    """The logical start time for the workflow run."""

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
    registry_lock: RegistryLock
    """Registry version lock from workflow definition. Required and must be non-empty."""

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
