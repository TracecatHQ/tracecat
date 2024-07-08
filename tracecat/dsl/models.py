from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

from tracecat.expressions.validation import TemplateValidator

SLUG_PATTERN = r"^[a-z0-9_]+$"
ACTION_TYPE_PATTERN = r"^[a-z0-9_.]+$"


class DSLNodeResult(TypedDict):
    result: Any
    result_typename: str


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

    args: dict[str, Any] = Field(
        default_factory=dict, description="Arguments for the action"
    )

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
    scheduler: Literal["static", "dynamic"] = "dynamic"
    enable_runtime_tests: bool = Field(
        default=False,
        description="Enable runtime action tests. This is dynamically set on workflow entry.",
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
