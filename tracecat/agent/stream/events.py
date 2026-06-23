from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

import orjson
from pydantic import Discriminator, TypeAdapter
from pydantic_ai.messages import AgentStreamEvent, ModelMessage, ModelResponse, TextPart

from tracecat.agent.common.stream_types import UnifiedStreamEvent

UnifiedStreamEventTA: TypeAdapter[UnifiedStreamEvent] = TypeAdapter(UnifiedStreamEvent)
AgentStreamEventTA: TypeAdapter[AgentStreamEvent] = TypeAdapter(AgentStreamEvent)


@dataclass(frozen=True, slots=True)
class VercelFrameCursor:
    """Browser SSE cursor for a Vercel frame fanned out from one Redis entry."""

    redis_id: str
    frame_index: int


def parse_vercel_frame_cursor(event_id: str | None) -> VercelFrameCursor | None:
    """Parse ``<redis-id>:<frame-index>`` cursors emitted by the Vercel adapter."""
    if not event_id:
        return None
    redis_id, separator, frame_index = event_id.rpartition(":")
    if not separator or not redis_id or not frame_index.isdecimal():
        return None
    return VercelFrameCursor(redis_id=redis_id, frame_index=int(frame_index))


@dataclass(slots=True, kw_only=True)
class StreamDelta:
    """Container for Redis stream payloads and adapter errors."""

    kind: Literal["event"] = "event"
    id: str
    event: UnifiedStreamEvent | AgentStreamEvent

    def sse(self) -> str:
        return f"id: {self.id}\nevent: delta\ndata: {orjson.dumps(self.event).decode()}\n\n"


@dataclass(slots=True, kw_only=True)
class StreamMessage:
    """Container for Redis stream payloads and adapter errors."""

    kind: Literal["message"] = "message"
    id: str
    message: ModelMessage

    def sse(self) -> str:
        return f"id: {self.id}\nevent: message\ndata: {orjson.dumps(self.message).decode()}\n\n"


@dataclass(slots=True, kw_only=True)
class StreamConnected:
    """Container for Redis stream payloads and adapter errors."""

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

    @staticmethod
    def model_response(err_msg: str) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content=StreamError.format(err_msg))], finish_reason="error"
        )


@dataclass(slots=True, kw_only=True)
class StreamKeepAlive:
    """Periodic keep-alive event to prevent proxy timeouts."""

    kind: Literal["keepalive"] = "keepalive"

    @staticmethod
    def sse() -> str:
        return ": keep-alive\n\n"


type StreamEvent = Annotated[
    StreamDelta | StreamMessage | StreamEnd | StreamError | StreamKeepAlive,
    Discriminator("kind"),
]

StreamFormat = Literal["vercel", "basic"]
