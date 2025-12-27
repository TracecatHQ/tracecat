from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class StreamEventType(str, Enum):
    """Types of streaming events."""

    # Text streaming
    TEXT_START = "text_start"
    TEXT_DELTA = "text_delta"
    TEXT_STOP = "text_stop"

    # Thinking/reasoning streaming
    THINKING_START = "thinking_start"
    THINKING_DELTA = "thinking_delta"
    THINKING_STOP = "thinking_stop"

    # Tool events
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_STOP = "tool_call_stop"
    TOOL_RESULT = "tool_result"

    # Lifecycle events
    MESSAGE_START = "message_start"
    MESSAGE_STOP = "message_stop"

    # Control events
    ERROR = "error"
    DONE = "done"
    APPROVAL_REQUEST = "approval_request"


class UnifiedStreamEvent(BaseModel):
    """A normalized streaming event.

    All harnesses convert their native events to this format.
    Format adapters (vercel, basic) can consume this directly.
    """

    type: StreamEventType
    part_id: int | None = Field(
        default=None,
        description="Index linking related events (e.g., start/delta/stop)",
    )

    # Flat payloads - only relevant ones are set based on event type
    text: str | None = None
    thinking: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: Any | None = None
    is_error: bool = False
    error: str | None = None
    approval_items: list[dict[str, Any]] | None = None

    timestamp: datetime = Field(default_factory=datetime.now)


class HarnessType(str, Enum):
    """Supported agent harnesses."""

    PYDANTIC_AI = "pydantic-ai"
    CLAUDE = "claude"
