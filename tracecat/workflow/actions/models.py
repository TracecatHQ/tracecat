from typing import Any

import orjson
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from tracecat.dsl.enums import JoinStrategy
from tracecat.dsl.models import ActionRetryPolicy
from tracecat.identifiers.action import ActionID
from tracecat.identifiers.workflow import WorkflowID


class ActionControlFlow(BaseModel):
    run_if: str | None = Field(default=None, max_length=1000)
    for_each: str | list[str] | None = Field(default=None, max_length=1000)
    retry_policy: ActionRetryPolicy = Field(default_factory=ActionRetryPolicy)
    start_delay: float = Field(
        default=0.0, description="Delay before starting the action in seconds."
    )
    join_strategy: JoinStrategy = Field(default=JoinStrategy.ALL)


class ActionRead(BaseModel):
    id: ActionID
    type: str
    title: str
    description: str
    status: str
    inputs: dict[str, Any]
    control_flow: ActionControlFlow = Field(default_factory=ActionControlFlow)


class ActionReadMinimal(BaseModel):
    id: ActionID
    workflow_id: WorkflowID
    type: str
    title: str
    description: str
    status: str


class ActionCreate(BaseModel):
    workflow_id: WorkflowID
    type: str
    title: str = Field(..., min_length=1, max_length=100)


class ActionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    status: str | None = None
    inputs: dict[str, Any] | None = None
    control_flow: ActionControlFlow | None = None

    @field_validator("inputs")
    def validate_inputs(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(orjson.dumps(v)) >= 10000:
            raise PydanticCustomError(
                "value_error.inputs_too_long",
                "Inputs must be less than 10000 characters",
                {"loc": ["inputs"]},
            )
        return v
