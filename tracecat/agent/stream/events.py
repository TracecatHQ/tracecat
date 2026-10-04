from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Literal

import orjson
from pydantic import Discriminator, TypeAdapter

from tracecat.agent.common.stream_types import UnifiedStreamEvent

UnifiedStreamEventTA: TypeAdapter[UnifiedStreamEvent] = TypeAdapter(UnifiedStreamEvent)


@dataclass(slots=True, kw_only=True)
class StreamDelta:
    """Container for Redis stream payloads and adapter errors."""

    kind: Literal["event"] = "event"
    id: str
    event: UnifiedStreamEvent

    def sse(self) -> str:
        return f"id: {self.id}\nevent: delta\ndata: {orjson.dumps(self.event).decode()}\n\n"


@dataclass(slots=True, kw_only=True)
class StreamSessionEvent:
    """An event addressed to a child session, carried on the parent's stream.

    Child events are rendered separately from the root transcript and must never
    be merged into it.
    """

    kind: Literal["session-event"] = "session-event"
    id: str
    """Redis entry id of the envelope on the parent's stream."""
    session_id: uuid.UUID
    """The child session the event belongs to."""
    event_id: str
    """Runtime-assigned stable id for the event, used for client dedupe."""
    event: UnifiedStreamEvent

    def sse(self) -> str:
        payload = orjson.dumps(
            {
                "session_id": str(self.session_id),
                "event_id": self.event_id,
                "event": self.event,
            }
        ).decode()
        return f"id: {self.id}\nevent: session-event\ndata: {payload}\n\n"


@dataclass(slots=True, kw_only=True)
class StreamConnected:
    kind: Literal["connected"] = "connected"
    id: str

    def sse(self) -> str:
        return f"id: {self.id}\nevent: connected\ndata: {{}}\n\n"


@dataclass(slots=True, kw_only=True)
class StreamEnd:
    """Container for Redis stream payloads and adapter errors."""

    kind: Literal["end-of-stream"] = "end-of-stream"
    id: str

    @staticmethod
    def sse() -> str:
        return "event: end\ndata: {}\n\n"


@dataclass(slots=True, kw_only=True)
class StreamError:
    """Container for Redis stream payloads and adapter errors."""

    kind: Literal["error"] = "error"
    error: str

    def sse(self) -> str:
        payload = orjson.dumps({"error": self.error}).decode()
        return f"event: error\ndata: {payload}\n\n"

    @staticmethod
    def format(err_msg: str) -> str:
        return f"The agent could not complete the request: {err_msg.strip()}"


@dataclass(slots=True, kw_only=True)
class StreamKeepAlive:
    """Periodic keep-alive event to prevent proxy timeouts."""

    kind: Literal["keepalive"] = "keepalive"

    @staticmethod
    def sse() -> str:
        return ": keep-alive\n\n"


type StreamEvent = Annotated[
    StreamDelta
    | StreamSessionEvent
    | StreamConnected
    | StreamEnd
    | StreamError
    | StreamKeepAlive,
    Discriminator("kind"),
]

StreamFormat = Literal["vercel", "basic"]
