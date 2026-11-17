"""Typed payloads of session commands, one per ``CommandType``."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, JsonValue, TypeAdapter

from tracecat.agent.control.enums import CommandType
from tracecat.core.schemas import Variant


class CommandPayloadBase(Variant, discriminator="type"):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SubmitPayload(CommandPayloadBase):
    """Start a turn with a user message."""

    type: Literal[CommandType.SUBMIT] = CommandType.SUBMIT
    input: str = Field(min_length=1, max_length=100_000)


class SteerPayload(CommandPayloadBase):
    """Add a user message to the running turn."""

    type: Literal[CommandType.STEER] = CommandType.STEER
    text: str = Field(min_length=1, max_length=100_000)


class DecidePayload(CommandPayloadBase):
    """Answer a pending approval; the command's request ID is the approval ID."""

    type: Literal[CommandType.DECIDE] = CommandType.DECIDE
    action: Literal["approve", "deny"]
    reason: str | None = Field(default=None, max_length=100_000)
    # Approve with these tool arguments instead of the requested ones.
    override_args: dict[str, JsonValue] | None = None


class AbortPayload(CommandPayloadBase):
    """Cancel the running turn."""

    type: Literal[CommandType.ABORT] = CommandType.ABORT


class WithdrawPayload(CommandPayloadBase):
    """Take back pending steers and queued messages the model has not read."""

    type: Literal[CommandType.WITHDRAW] = CommandType.WITHDRAW
    # Request IDs to take back; None takes back all of them.
    target_ids: list[str] | None = Field(default=None, max_length=1_000)


type CommandPayload = Annotated[
    SubmitPayload | SteerPayload | DecidePayload | AbortPayload | WithdrawPayload,
    Field(discriminator="type"),
]

COMMAND_PAYLOAD: TypeAdapter[CommandPayload] = TypeAdapter(CommandPayload)
