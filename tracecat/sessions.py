from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class Session[TEvent: Any](BaseModel):
    """A generic session that emits events. Use this to model Agent sessions/chat, RTR, sandbox streams, etc."""

    id: uuid.UUID
    events: list[TEvent] | None = Field(
        default=None, description="The events in the session."
    )
