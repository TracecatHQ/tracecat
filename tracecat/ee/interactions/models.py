from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from tracecat.ee.enums import PlatformAction
from tracecat.ee.interactions.enums import SignalStatus


class WaitResponseArgs(BaseModel):
    """The arguments for the `core.wait.response` action."""

    ref: str
    """The reference to the action that will receive the response."""

    channel: str | None = None
    """The communication channel to await the response on."""

    timeout: float | None = None
    """The timeout for the response."""


class SignalHandlerInput(BaseModel):
    """Input for the workflow signal handler. This is used on the client side."""

    signal_id: str
    """The signal ID."""

    ref: str
    """The action reference of the signal sender."""

    data: dict[str, Any]
    """Data passed to the signal handler."""


class SignalHandlerResult(BaseModel):
    """Output for the workflow signal handler. This is used on the client side."""

    message: str
    """The message of the signal handler."""

    detail: Any | None = None
    """The detail of the signal handler."""


@dataclass
class SignalState:
    """A signal state."""

    ref: str
    """The action reference of the signal receiver."""

    type: Literal[PlatformAction.WAIT_RESPONSE] = PlatformAction.WAIT_RESPONSE
    """The signal type. Response, approval, etc."""

    status: SignalStatus = SignalStatus.IDLE
    """The status of the signal."""

    data: dict[str, Any] = field(default_factory=dict)
    """The data passed to the signal handler."""

    def is_activated(self) -> bool:
        return self.status == SignalStatus.COMPLETED
