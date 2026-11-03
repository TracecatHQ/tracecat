"""Contract for delivering agent output to a channel.

The enterprise worker registers this activity. Agent workflows call it only for
inputs whose origin is ``ChannelOrigin``, which only enterprise ingress
produces, so no registry check is needed.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from tracecat.agent.session.inputs import Actor
from tracecat.core.schemas import Variant

DELIVER_CHANNEL_OUTPUT_ACTIVITY = "deliver_channel_output"


class ChannelOutputBase(Variant, discriminator="kind"):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Reply(ChannelOutputBase):
    kind: Literal["reply"] = "reply"
    run_id: uuid.UUID
    # Bumps at each steer boundary.
    segment: int
    text: str


class Status(ChannelOutputBase):
    kind: Literal["status"] = "status"
    run_id: uuid.UUID
    text: str


class ApprovalCard(ChannelOutputBase):
    kind: Literal["approval_card"] = "approval_card"
    run_id: uuid.UUID
    batch_id: uuid.UUID
    tool_call_id: str
    tool: str
    state: Literal["pending", "approved", "rejected"]
    decided_by: Actor | None = None


class MirroredMessage(ChannelOutputBase):
    kind: Literal["mirrored_message"] = "mirrored_message"
    message_id: str
    author: Actor
    author_name: str
    text: str


ChannelOutput = Annotated[
    Reply | Status | ApprovalCard | MirroredMessage, Field(discriminator="kind")
]


class DeliverChannelOutput(BaseModel):
    """Request for the channel delivery activity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: uuid.UUID
    session_id: uuid.UUID
    output: ChannelOutput
    # Names a message slot, reused across edits.
    op_id: str
    # Opaque provider message id; set to edit that message in place.
    previous_ref: str | None = None
