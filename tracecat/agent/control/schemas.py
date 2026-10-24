"""API contract for controlling a session's running turn."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

type AgentControlType = Literal["steer", "withdraw", "cancel"]
type AgentControlStatus = Literal["accepted", "rejected", "withdrawn", "too_late"]


class AgentSteerControl(BaseModel):
    """A message for the running turn to take into account before it answers."""

    type: Literal["steer"]
    id: uuid.UUID = Field(
        description=(
            "Client-generated message ID. It reaches at most one turn of the "
            "session; a retry returns the first outcome."
        )
    )
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000)
    ]


class AgentWithdrawControl(BaseModel):
    """Take back pending user messages the model has not read yet."""

    type: Literal["withdraw"]
    id: uuid.UUID = Field(
        description="Client-generated request ID; a retry returns the first outcome"
    )
    target_ids: list[uuid.UUID] | None = Field(
        default=None,
        description=(
            "Message IDs to withdraw. Null withdraws all of the session's pending "
            "steers and queued messages."
        ),
    )


class AgentCancelControl(BaseModel):
    """Cancel the running turn."""

    type: Literal["cancel"]
    id: uuid.UUID = Field(description="Client-generated request ID")


type AgentControlRequest = Annotated[
    AgentSteerControl | AgentWithdrawControl | AgentCancelControl,
    Field(discriminator="type"),
]


class AgentControlReceipt(BaseModel):
    """The running turn's outcome for a control request."""

    id: uuid.UUID
    type: AgentControlType
    run_id: uuid.UUID
    status: AgentControlStatus
    withdrawn: list[uuid.UUID] = Field(
        default_factory=list,
        description="Withdrawn message IDs in send order; set only for `withdraw`",
    )
    too_late: list[uuid.UUID] = Field(
        default_factory=list,
        description=(
            "Message IDs the model already read, which stay in the transcript; "
            "set only for `withdraw`"
        ),
    )
