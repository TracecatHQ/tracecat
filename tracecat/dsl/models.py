from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Generic, Literal, TypedDict, TypeVar

from pydantic import BaseModel, Field

from tracecat.contexts import RunContext
from tracecat.dsl.constants import DEFAULT_ACTION_TIMEOUT
from tracecat.dsl.enums import JoinStrategy
from tracecat.expressions.shared import ExprContext
from tracecat.expressions.validation import ExpressionStr, TemplateValidator
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.types.auth import Role
from tracecat.webhooks.models import TriggerInputs

SLUG_PATTERN = r"^[a-z0-9_]+$"
ACTION_TYPE_PATTERN = r"^[a-z0-9_.]+$"


class DSLNodeResult(TypedDict, total=False):
    """Result of executing a DSL node."""

    result: Any
    result_typename: str
    error: Any | None
    error_typename: str | None


@dataclass(frozen=True)
class DSLTaskErrorInfo:
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


ArgsT = TypeVar("ArgsT", bound=Mapping[str, Any])


class ActionRetryPolicy(BaseModel):
    max_attempts: int = Field(
        default=1,
        description="Total number of execution attempts. 0 means unlimited, 1 means no retries.",
    )
    timeout: int = Field(
        default=DEFAULT_ACTION_TIMEOUT, description="Timeout for the action in seconds."
    )


class ActionStatement(BaseModel, Generic[ArgsT]):
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

    args: ArgsT = Field(default_factory=dict, description="Arguments for the action")

    depends_on: list[str] = Field(default_factory=list, description="Task dependencies")

    """Control flow options"""

    run_if: Annotated[
        str | None,
        Field(default=None, description="Condition to run the task"),
        TemplateValidator(),
    ]

    for_each: Annotated[
        str | list[str] | None,
        Field(
            default=None,
            description="Iterate over a list of items and run the task for each item.",
        ),
        TemplateValidator(),
    ]
    retry_policy: ActionRetryPolicy = Field(
        default_factory=ActionRetryPolicy, description="Retry policy for the action."
    )
    start_delay: float = Field(
        default=0.0, description="Delay before starting the action in seconds."
    )
    join_strategy: JoinStrategy = Field(
        default=JoinStrategy.ALL,
        description=(
            "The strategy to use when joining on this task. "
            "By default, all branches must complete successfully before the join task can complete."
        ),
    )

    @property
    def title(self) -> str:
        return self.ref.capitalize().replace("_", " ")


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


class DSLContext(TypedDict, total=False):
    INPUTS: dict[str, Any]
    """DSL Static Inputs context"""

    ACTIONS: dict[str, Any]
    """DSL Actions context"""

    TRIGGER: TriggerInputs
    """DSL Trigger dynamic inputs context"""

    ENV: DSLEnvironment
    """DSL Environment context. Has metadata about the workflow."""

    @staticmethod
    def create_default(
        INPUTS: dict[str, Any] | None = None,
        ACTIONS: dict[str, Any] | None = None,
        TRIGGER: dict[str, Any] | None = None,
        ENV: dict[str, Any] | None = None,
    ) -> DSLContext:
        return DSLContext(
            INPUTS=INPUTS or {},
            ACTIONS=ACTIONS or {},
            TRIGGER=TRIGGER or {},
            ENV=ENV or {},
        )


class RunActionInput(BaseModel, Generic[ArgsT]):
    """This object contains all the information needed to execute an action."""

    task: ActionStatement[ArgsT]
    role: Role
    exec_context: DSLContext
    run_context: RunContext


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

    @staticmethod
    def from_exception(e: BaseException) -> DSLExecutionError:
        return DSLExecutionError(
            is_error=True,
            type=e.__class__.__name__,
            message=str(e),
        )
