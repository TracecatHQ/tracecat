from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field, field_validator, model_validator

from tracecat.dsl.constants import DEFAULT_ACTION_TIMEOUT
from tracecat.dsl.enums import JoinStrategy
from tracecat.ee.interactions.models import ActionInteraction, InteractionContext
from tracecat.expressions.common import ExprContext
from tracecat.expressions.validation import ExpressionStr, RequiredExpressionStr
from tracecat.identifiers import WorkflowExecutionID, WorkflowRunID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowUUID
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.types.exceptions import TracecatValidationError

SLUG_PATTERN = r"^[a-z0-9_]+$"
ACTION_TYPE_PATTERN = r"^[a-z0-9_.]+$"

TriggerInputs = Any
"""Trigger inputs JSON type."""

ExecutionContext = dict[ExprContext, Any]
"""Workflow execution context."""


class TaskResult(TypedDict, total=False):
    """Result of executing a DSL node."""

    result: Any
    result_typename: str
    error: Any | None
    error_typename: str | None
    interaction: Any | None
    interaction_id: str | None
    interaction_type: str | None


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
        default=300,
        description="The maximum number of seconds to wait for the workflow to complete.",
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


class RunActionInput(BaseModel):
    """This object contains all the information needed to execute an action."""

    task: ActionStatement
    exec_context: ExecutionContext
    run_context: RunContext
    # This gets passed in from the worker
    interaction_context: InteractionContext | None = None


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
    details: ActionErrorInfo | None = None
