"""What the session service reports back for a message, a decision, or a stop.

The prefix names the thing whose state is reported; failures raise.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from tracecat.core.schemas import Variant


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

    pending_tool_call_ids: Sequence[str]
    resumed: RunRef | None
