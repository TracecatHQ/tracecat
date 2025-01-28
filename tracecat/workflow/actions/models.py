from pydantic import BaseModel, Field

from tracecat.dsl.enums import JoinStrategy
from tracecat.dsl.models import ActionRetryPolicy
from tracecat.identifiers.action import ActionID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowIDShort


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
    inputs: str
    control_flow: ActionControlFlow = Field(default_factory=ActionControlFlow)


class ActionReadMinimal(BaseModel):
    id: ActionID
    workflow_id: WorkflowIDShort
    type: str
    title: str
    description: str
    status: str


class ActionCreate(BaseModel):
    workflow_id: AnyWorkflowID
    type: str
    title: str = Field(..., min_length=1, max_length=100)


class ActionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    status: str | None = None
    inputs: str = Field(default="", max_length=10000)
    control_flow: ActionControlFlow | None = None
