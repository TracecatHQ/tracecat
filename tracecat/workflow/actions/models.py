from typing import Any

from pydantic import BaseModel, Field

from tracecat.dsl.enums import JoinStrategy
from tracecat.dsl.models import ActionRetryPolicy


class ActionControlFlow(BaseModel):
    run_if: str | None = None
    for_each: str | list[str] | None = None
    retry_policy: ActionRetryPolicy = Field(default_factory=ActionRetryPolicy)
    start_delay: float = Field(
        default=0.0, description="Delay before starting the action in seconds."
    )
    join_strategy: JoinStrategy = Field(default=JoinStrategy.ALL)


class ActionRead(BaseModel):
    id: str
    type: str
    title: str
    description: str
    status: str
    inputs: dict[str, Any]
    control_flow: ActionControlFlow = Field(default_factory=ActionControlFlow)


class ActionReadMinimal(BaseModel):
    id: str
    workflow_id: str
    type: str
    title: str
    description: str
    status: str


class ActionCreate(BaseModel):
    workflow_id: str
    type: str
    title: str = Field(min_length=1, max_length=100)


class ActionUpdate(BaseModel):
    title: str | None = Field(min_length=1, max_length=100)
    description: str | None = Field(max_length=1000)
    status: str | None = None
    inputs: dict[str, Any] | None = None
    control_flow: ActionControlFlow | None = None
