from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Generic, Literal, TypedDict, TypeVar

from pydantic import BaseModel, Field
from tracecat_registry import REGISTRY_VERSION

from tracecat.contexts import RunContext
from tracecat.expressions.validation import ExpressionStr, TemplateValidator
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.types.auth import Role

SLUG_PATTERN = r"^[a-z0-9_]+$"
ACTION_TYPE_PATTERN = r"^[a-z0-9_.]+$"


class DSLNodeResult(TypedDict):
    result: Any
    result_typename: str


ArgsT = TypeVar("ArgsT", bound=Mapping[str, Any])


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
    enable_runtime_tests: bool = Field(
        default=False,
        description="Enable runtime action tests. This is dynamically set on workflow entry.",
    )
    environment: ExpressionStr = Field(
        default=DEFAULT_SECRETS_ENVIRONMENT,
        description=(
            "The workflow's target execution environment. "
            "This is used to isolate secrets across different environments."
            "If not provided, the default environment (default) is used."
        ),
    )
    registry_version: str = Field(
        default=REGISTRY_VERSION,
        description="The registry version to use for the workflow.",
    )


class Trigger(BaseModel):
    type: Literal["schedule", "webhook"]
    ref: str = Field(pattern=SLUG_PATTERN)
    args: dict[str, Any] = Field(default_factory=dict)


class ActionTest(BaseModel):
    ref: str = Field(..., pattern=SLUG_PATTERN, description="Action reference")
    enable: bool = True
    validate_args: bool = True
    success: Any = Field(
        ...,
        description=(
            "Patched success output. This can be any data structure."
            "If it's a fsspec file, it will be read and the contents will be used."
        ),
    )
    failure: Any = Field(default=None, description="Patched failure output")


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

    TRIGGER: dict[str, Any]
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


class UDFActionInput(BaseModel, Generic[ArgsT]):
    """This object contains all the information needed to execute an action."""

    task: ActionStatement[ArgsT]
    role: Role
    exec_context: DSLContext
    run_context: RunContext
    action_test: ActionTest | None = None
