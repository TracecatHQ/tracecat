import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from tracecat.ee.interactions.enums import InteractionStatus, InteractionType
from tracecat.expressions.validation import ExpressionStr
from tracecat.identifiers.workflow import WorkflowExecutionID
from tracecat.types.auth import Role


class InteractionContext(BaseModel):
    """The context of the interaction."""

    interaction_id: uuid.UUID
    """The interaction ID."""

    execution_id: WorkflowExecutionID
    """The workflow execution ID."""

    # NOTE: Maybe we can remove this
    action_ref: str
    """The action reference of the interaction sender."""


class InteractionInput(InteractionContext):
    """Input for the workflow interaction handler. This is used on the client side."""

    data: dict[str, Any]
    """Data passed to the interaction handler."""


class InteractionResult(BaseModel):
    """Output for the workflow interaction handler. This is used on the client side."""

    message: str
    """The message of the interaction handler."""

    detail: Any | None = None
    """The detail of the interaction handler."""


@dataclass
class InteractionState:
    """An interaction state."""

    type: InteractionType
    """The interaction type. Response, approval, etc."""

    action_ref: str
    """The action reference of the interaction sender."""

    status: InteractionStatus
    """The status of the interaction."""

    data: dict[str, Any] = field(default_factory=dict)
    """The data passed to the interaction handler."""

    def is_activated(self) -> bool:
        return self.status == InteractionStatus.COMPLETED


class ResponseInteraction(BaseModel):
    """Configuration for a response interaction."""

    type: Literal[InteractionType.RESPONSE]
    timeout: float | None = Field(
        default=None,
        description="The timeout for the interaction in seconds.",
    )


# TODO: This is a placeholder of a possible future interaction type
# We could have other kinds of interactions like forms, MFA, etc.
class ApprovalInteraction(BaseModel):
    """Configuration for an approval interaction."""

    type: Literal[InteractionType.APPROVAL]
    timeout: float | None = Field(
        default=None,
        description="The timeout for the interaction in seconds.",
    )
    required_approvers: int = Field(
        default=1,
        description="Number of approvers required before the action can proceed.",
    )
    approver_groups: list[str] = Field(
        default_factory=list,
        description="List of groups that are allowed to approve this action.",
    )
    message: str = Field(
        default="",
        description="Custom message to display to approvers.",
    )
    approve_if: ExpressionStr | None = Field(
        default=None,
        description="Condition to approve the action.",
    )


ActionInteraction = Annotated[
    ResponseInteraction | ApprovalInteraction,
    Field(
        discriminator="type",
        description="An interaction configuration",
    ),
]
ActionInteractionValidator: TypeAdapter[ActionInteraction] = TypeAdapter(
    ActionInteraction
)


# Api Service


class InteractionRead(BaseModel):
    """Model for reading an interaction."""

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    type: InteractionType
    status: InteractionStatus
    request_payload: dict[str, Any] | None
    response_payload: dict[str, Any] | None
    expires_at: datetime | None = None
    # Where this came from
    wf_exec_id: WorkflowExecutionID
    actor: str | None
    action_ref: str
    action_type: str


class InteractionCreate(BaseModel):
    """Model for creating a new interaction."""

    type: InteractionType
    status: InteractionStatus
    request_payload: dict[str, Any] | None = None
    response_payload: dict[str, Any] | None = None
    expires_at: datetime | None = None
    actor: str | None = None
    wf_exec_id: WorkflowExecutionID
    action_ref: str
    action_type: str


class InteractionUpdate(BaseModel):
    """Model for updating an interaction."""

    status: InteractionStatus | None = None
    response_payload: dict[str, Any] | None = None
    actor: str | None = None


class CreateInteractionActivityInputs(BaseModel):
    """Inputs for the create interaction activity."""

    role: Role
    params: InteractionCreate


class UpdateInteractionActivityInputs(BaseModel):
    """Inputs for the update interaction activity."""

    role: Role
    interaction_id: uuid.UUID
    params: InteractionUpdate
