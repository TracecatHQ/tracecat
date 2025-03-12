from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from tracecat.ee.enums import PlatformAction
from tracecat.ee.interactions.enums import InteractionStatus, InteractionType
from tracecat.identifiers.workflow import WorkflowExecutionID


class WaitResponseArgs(BaseModel):
    """The arguments for the `core.wait.response` action."""

    interaction_id: str
    """The reference to the action that will receive the response."""

    channel: str | None = None
    """The communication channel to await the response on."""

    timeout: float | None = None
    """The timeout for the response."""


class InteractionContext(BaseModel):
    """The context of the interaction."""

    interaction_id: str
    """The interaction ID."""

    execution_id: WorkflowExecutionID
    """The workflow execution ID."""

    ref: str
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

    ref: str
    """The action reference of the interaction receiver."""

    type: Literal[PlatformAction.WAIT_RESPONSE] = PlatformAction.WAIT_RESPONSE
    """The interaction type. Response, approval, etc."""

    status: InteractionStatus = InteractionStatus.IDLE
    """The status of the interaction."""

    data: dict[str, Any] = field(default_factory=dict)
    """The data passed to the interaction handler."""

    def is_activated(self) -> bool:
        return self.status == InteractionStatus.COMPLETED


class ApprovalEvent(BaseModel):
    """An approval event."""

    type: Literal[InteractionType.APPROVAL] = Field(
        default=InteractionType.APPROVAL, frozen=True
    )
    payload: dict[str, Any] | None = None


class ResponseEvent(BaseModel):
    """A response event."""

    type: Literal[InteractionType.RESPONSE] = Field(
        default=InteractionType.RESPONSE, frozen=True
    )
    signal_name: str
    payload: dict[str, Any] | None = None


# Use pydantic TypeAdapter
type InteractionEvent = Annotated[
    ApprovalEvent | ResponseEvent, Field(discriminator="type")
]
InteractionEventValidator: TypeAdapter[InteractionEvent] = TypeAdapter(InteractionEvent)
