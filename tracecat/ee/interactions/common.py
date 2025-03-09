from dataclasses import dataclass, field
from typing import Any, Literal

from tracecat.ee.enums import PlatformAction
from tracecat.ee.interactions.enums import SignalStatus


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
