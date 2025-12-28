from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCallContent(BaseModel):
    """Structured tool call for approval requests.

    This is the harness-agnostic representation of a tool call
    that requires approval before execution.
    """

    type: Literal["tool_call"] = "tool_call"
    id: str = Field(..., description="Unique tool call ID")
    name: str = Field(..., description="Fully-qualified tool name")
    input: dict[str, Any] = Field(
        default_factory=dict, description="Arguments for the tool call"
    )


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
    USER_MESSAGE = "user_message"

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

    # For APPROVAL_REQUEST events
    approval_items: list[ToolCallContent] | None = None

    timestamp: datetime = Field(default_factory=datetime.now)

    @classmethod
    def approval_request_event(cls, items: list[ToolCallContent]) -> UnifiedStreamEvent:
        """Factory method for creating approval request events.

        Args:
            items: List of tool calls that require approval.

        Returns:
            A UnifiedStreamEvent with type APPROVAL_REQUEST.
        """
        return cls(type=StreamEventType.APPROVAL_REQUEST, approval_items=items)

    @classmethod
    def user_message_event(cls, content: str) -> UnifiedStreamEvent:
        """Factory method for creating user message events.

        Args:
            content: The user's message text.

        Returns:
            A UnifiedStreamEvent with type USER_MESSAGE.
        """
        return cls(type=StreamEventType.USER_MESSAGE, text=content)


class HarnessType(str, Enum):
    """Supported agent harnesses."""

    PYDANTIC_AI = "pydantic-ai"
    CLAUDE = "claude"
