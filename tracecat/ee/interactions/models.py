from typing import Any

from pydantic import BaseModel


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
