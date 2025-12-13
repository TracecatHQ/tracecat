from typing import Literal, NotRequired, TypedDict

import dateparser
import yaml
from pydantic import Field, TypeAdapter, computed_field, field_validator

from tracecat.core.schemas import Schema
from tracecat.dsl.enums import JoinStrategy
from tracecat.dsl.schemas import ActionRetryPolicy
from tracecat.dsl.view import Position
from tracecat.identifiers.action import ActionID
from tracecat.identifiers.action import ref as _ref
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowIDShort
from tracecat.interactions.schemas import ActionInteraction


class ActionEdge(TypedDict):
    """Represents an incoming edge to an action.

    Stored in Action.upstream_edges to represent incoming connections.
    """

    source_id: str
    """Source node ID. For trigger: 'trigger-{workflow_uuid}', for action: action UUID."""

    source_type: Literal["trigger", "udf"]
    """Type of source node."""

    source_handle: NotRequired[Literal["success", "error"]]
    """Edge handle type. Only present for 'udf' source types."""


ActionEdgesTA: TypeAdapter[list[ActionEdge]] = TypeAdapter(list[ActionEdge])
"""TypeAdapter for validating upstream_edges from the database."""


class ActionControlFlow(Schema):
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
    environment: str | None = Field(
        default=None,
        description="Override environment for this action's execution",
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


class ActionRead(Schema):
    id: ActionID
    type: str
    title: str
    description: str
    status: str
    inputs: str
    control_flow: ActionControlFlow = Field(default_factory=ActionControlFlow)
    is_interactive: bool
    interaction: ActionInteraction | None = None
    position_x: float = 0.0
    position_y: float = 0.0
    upstream_edges: list[ActionEdge] = Field(default_factory=list)

    @computed_field
    def ref(self) -> str:
        return _ref(self.title)


class ActionReadMinimal(Schema):
    id: ActionID
    workflow_id: WorkflowIDShort
    type: str
    title: str
    description: str
    status: str
    is_interactive: bool


class ActionCreate(Schema):
    workflow_id: AnyWorkflowID
    type: str
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    inputs: str = Field(default="", max_length=300000)
    control_flow: ActionControlFlow | None = Field(
        default=None, json_schema_extra={"mode": "json"}
    )
    is_interactive: bool = Field(default=False)
    interaction: ActionInteraction | None = None
    position_x: float = Field(default=0.0)
    position_y: float = Field(default=0.0)
    upstream_edges: list[ActionEdge] = Field(default_factory=list)

    @field_validator("inputs", mode="after")
    def validate_inputs(cls, v: str) -> str:
        try:
            yaml.safe_load(v)
        except yaml.YAMLError:
            raise ValueError("Action input contains invalid YAML") from None
        return v


class ActionUpdate(Schema):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    status: str | None = None
    inputs: str = Field(default="", max_length=300000)
    control_flow: ActionControlFlow | None = Field(
        default=None, json_schema_extra={"mode": "json"}
    )
    is_interactive: bool | None = None
    interaction: ActionInteraction | None = None
    position_x: float | None = None
    position_y: float | None = None
    upstream_edges: list[ActionEdge] | None = None

    @field_validator("inputs", mode="after")
    def validate_inputs(cls, v: str) -> str:
        try:
            yaml.safe_load(v)
        except yaml.YAMLError:
            raise ValueError("Action input contains invalid YAML") from None
        return v


class ActionPositionUpdate(Schema):
    """Position update for a single action."""

    action_id: ActionID
    position: Position


class BatchPositionUpdate(Schema):
    """Batch update for action and trigger positions."""

    actions: list[ActionPositionUpdate] = Field(default_factory=list)
    trigger_position: Position | None = None
