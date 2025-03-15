import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from tracecat.ee.interactions.enums import InteractionStatus, InteractionType
from tracecat.expressions.validation import ExpressionStr
from tracecat.identifiers.workflow import WorkflowExecutionID


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
