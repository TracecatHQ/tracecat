from typing import Any, Literal

from pydantic import BaseModel, Field, JsonValue

from tracecat.dsl.common import DSLInput
from tracecat.dsl.models import ExecutionContext
from tracecat.registry.actions.models import RegistryActionValidateResponse

type Status = Literal["success", "error"]


class BuilderResource(BaseModel):
    """A resource in the builder DSL."""

    id: str = Field(description="The ID of the resource.")

    content_type: str = Field(
        description="The content type of the resource.",
        examples=[
            "application/vnd.tc+json",
            "application/vnd.tc.action+json",
            "application/vnd.tc.dsl+lark",
        ],
    )

    data: Any = Field(description="The data of the resource.")


class BuilderActionRun(BaseModel):
    """Input for running an action in a builder environment."""

    action: str
    args: dict[str, Any]
    context: ExecutionContext = Field(default_factory=dict)


class BuilderActionRunResult(BaseModel):
    """Result of running an action."""

    status: Status = Field(description="The status of the action.")
    message: str = Field(description="The message of the action.")
    result: Any | None = Field(default=None, description="The result of the action.")
    error: Any | None = Field(default=None, description="The error of the action.")


class BuilderWorkflowDefinitionValidate(BaseModel):
    """Input for validating a workflow definition."""

    dsl: DSLInput


class BuilderWorkflowDefinitionValidateResult(BaseModel):
    status: Status = Field(description="The status of the validation.")
    message: str = Field(description="The message of the validation.")
    errors: list[RegistryActionValidateResponse] = Field(
        default_factory=list,
        description="Any errors that occurred during the validation.",
    )


class BuilderWorkflowExecute(BaseModel):
    """Input for executing a workflow."""

    dsl: DSLInput
    trigger_inputs: JsonValue


class BuilderWorkflowExecuteResult(BaseModel):
    """Result of executing a workflow."""

    wf_id: str = Field(description="The ID of the execution.")
    status: Status = Field(description="The status of the execution.")
    message: str = Field(description="The message of the execution.")
    result: Any | None = Field(default=None, description="The result of the execution.")
    error: Any | None = Field(default=None, description="The error of the execution.")
