import dateparser
from pydantic import BaseModel, Field, field_validator

from tracecat.dsl.enums import JoinStrategy
from tracecat.dsl.models import ActionRetryPolicy
from tracecat.identifiers.action import ActionID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowIDShort


class ActionControlFlow(BaseModel):
    run_if: str | None = Field(default=None, max_length=1000)
    for_each: str | list[str] | None = Field(default=None, max_length=1000)
    join_strategy: JoinStrategy = Field(default=JoinStrategy.ALL)
    # Retries
    retry_policy: ActionRetryPolicy = Field(default_factory=ActionRetryPolicy)
    # Timers
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

    @field_validator("wait_until", mode="before")
    def validate_wait_until(cls, v: str | None) -> str | None:
        if v is not None:
            if (
                dateparser.parse(
                    v, settings={"TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True}
                )
                is None
            ):
                raise ValueError(f"'{v}' is not a valid `wait_until` value")
        return v


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
