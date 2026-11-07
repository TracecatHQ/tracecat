"""Input verbs a session accepts, and the results they report.

Ingress intent, not commands: the service maps message to submit or steer by
run state, decision to decide, and stop to abort (``tracecat.agent.control``).
Stored in Temporal history: evolve additively with defaults so old history
still validates; unknown fields stay rejected.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from tracecat.core.schemas import Variant


class Actor(BaseModel):
    """Who acted: a Tracecat user, a channel user, both, or neither."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: uuid.UUID | None
    channel_user_id: uuid.UUID | None


class InputOriginBase(Variant, discriminator="kind"):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WebOrigin(InputOriginBase):
    kind: Literal["web"] = "web"


class ChannelOrigin(InputOriginBase):
    """An input from an installed channel extension."""

    kind: Literal["channel"] = "channel"
    # Opaque here: the extension resolves the thread from the session.
    provider: str
    bot_id: uuid.UUID


class ScheduleOrigin(InputOriginBase):
    kind: Literal["schedule"] = "schedule"
    schedule_id: str


InputOrigin = Annotated[
    WebOrigin | ChannelOrigin | ScheduleOrigin, Field(discriminator="kind")
]


class AgentSessionInput(Variant, discriminator="kind"):
    """Fields shared by every input verb on a session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Stable across retries; no principal: the run executes as the service's run role.
    request_id: str
    session_id: uuid.UUID
    actor: Actor
    origin: InputOrigin


class MessageInput(AgentSessionInput):
    kind: Literal["message"] = "message"
    text: str


class DecisionInput(AgentSessionInput):
    kind: Literal["decision"] = "decision"
    batch_id: uuid.UUID
    tool_call_id: str
    action: Literal["approve", "deny"]
    reason: str | None = None


class StopInput(AgentSessionInput):
    kind: Literal["stop"] = "stop"
    # A stale click must not stop a newer run.
    expected_run_id: uuid.UUID


SessionInput = Annotated[
    MessageInput | DecisionInput | StopInput, Field(discriminator="kind")
]


# Results: the prefix names the thing whose state is reported; failures raise.


class SessionResultBase(Variant, discriminator="kind"):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RunStarted(SessionResultBase):
    kind: Literal["run_started"] = "run_started"
    run_id: uuid.UUID


class RunSteered(SessionResultBase):
    kind: Literal["run_steered"] = "run_steered"
    run_id: uuid.UUID


class RunBusy(SessionResultBase):
    """A run is going and the harness cannot steer it."""

    kind: Literal["run_busy"] = "run_busy"
    run_id: uuid.UUID


MessageResult = Annotated[
    RunStarted | RunSteered | RunBusy, Field(discriminator="kind")
]


class RunStopping(SessionResultBase):
    kind: Literal["run_stopping"] = "run_stopping"
    run_id: uuid.UUID


class RunAlreadyFinished(SessionResultBase):
    kind: Literal["run_already_finished"] = "run_already_finished"
    run_id: uuid.UUID


class RunSuperseded(SessionResultBase):
    kind: Literal["run_superseded"] = "run_superseded"
    current_run_id: uuid.UUID


StopResult = Annotated[
    RunStopping | RunAlreadyFinished | RunSuperseded, Field(discriminator="kind")
]


class RunRef(BaseModel):
    """A run and the stream that carries its output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: uuid.UUID
    stream_id: uuid.UUID


class DecisionRecorded(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pending_tool_call_ids: list[str]
    resumed: RunRef | None
