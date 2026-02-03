"""Lightweight stream types for agent communication.

Pure dataclasses with no Pydantic dependencies for minimal import footprint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal


class HarnessType(StrEnum):
    """Supported agent harnesses."""

    PYDANTIC_AI = "pydantic-ai"
    CLAUDE_CODE = "claude_code"


class StreamEventType(StrEnum):
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


@dataclass(kw_only=True, slots=True)
class ToolCallContent:
    """Structured tool call for approval requests.

    This is the harness-agnostic representation of a tool call
    that requires approval before execution.
    """

    type: Literal["tool_call"] = "tool_call"
    id: str
    """Unique tool call ID."""
    name: str
    """Fully-qualified tool name."""
    input: dict[str, Any] = field(default_factory=dict)
    """Arguments for the tool call."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCallContent:
        """Construct from dict (orjson parsed)."""
        return cls(
            type=data.get("type", "tool_call"),
            id=data["id"],
            name=data["name"],
            input=data.get("input", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for orjson serialization."""
        return {
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "input": self.input,
        }


@dataclass(kw_only=True, slots=True)
class UnifiedStreamEvent:
    """A normalized streaming event.

    All harnesses convert their native events to this format.
    Format adapters (vercel, basic) can consume this directly.
    """

    type: StreamEventType
    part_id: int | None = None
    """Index linking related events (e.g., start/delta/stop)."""

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

    timestamp: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnifiedStreamEvent:
        """Construct from dict (orjson parsed)."""
        approval_items = None
        if data.get("approval_items"):
            approval_items = [
                ToolCallContent.from_dict(item) for item in data["approval_items"]
            ]

        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        elif timestamp is None:
            timestamp = datetime.now(UTC)

        return cls(
            type=StreamEventType(data["type"]),
            part_id=data.get("part_id"),
            text=data.get("text"),
            thinking=data.get("thinking"),
            tool_call_id=data.get("tool_call_id"),
            tool_name=data.get("tool_name"),
            tool_input=data.get("tool_input"),
            tool_output=data.get("tool_output"),
            is_error=data.get("is_error", False),
            error=data.get("error"),
            approval_items=approval_items,
            timestamp=timestamp,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for orjson serialization."""
        result: dict[str, Any] = {
            "type": self.type.value,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.part_id is not None:
            result["part_id"] = self.part_id
        if self.text is not None:
            result["text"] = self.text
        if self.thinking is not None:
            result["thinking"] = self.thinking
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        if self.tool_name is not None:
            result["tool_name"] = self.tool_name
        if self.tool_input is not None:
            result["tool_input"] = self.tool_input
        if self.tool_output is not None:
            result["tool_output"] = self.tool_output
        if self.is_error:
            result["is_error"] = self.is_error
        if self.error is not None:
            result["error"] = self.error
        if self.approval_items is not None:
            result["approval_items"] = [item.to_dict() for item in self.approval_items]
        return result

    @classmethod
    def approval_request_event(cls, items: list[ToolCallContent]) -> UnifiedStreamEvent:
        """Factory method for creating approval request events."""
        return cls(type=StreamEventType.APPROVAL_REQUEST, approval_items=items)

    @classmethod
    def user_message_event(cls, content: str) -> UnifiedStreamEvent:
        """Factory method for creating user message events."""
        return cls(type=StreamEventType.USER_MESSAGE, text=content)

    @classmethod
    def tool_result_event(
        cls,
        tool_call_id: str,
        tool_name: str,
        output: Any,
        is_error: bool = False,
    ) -> UnifiedStreamEvent:
        """Factory method for creating tool result events."""
        return cls(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_output=output,
            is_error=is_error,
        )
