from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

import orjson
from pydantic import Discriminator, TypeAdapter
from pydantic_ai.messages import AgentStreamEvent, ModelMessage, ModelResponse, TextPart

AgentStreamEventTA: TypeAdapter[AgentStreamEvent] = TypeAdapter(AgentStreamEvent)


@dataclass(slots=True, kw_only=True)
class StreamDelta:
    """Container for Redis stream payloads and adapter errors."""

    kind: Literal["event"] = "event"
    id: str
    event: AgentStreamEvent

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
