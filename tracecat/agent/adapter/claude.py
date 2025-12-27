"""Claude Agent SDK harness adapter.

Converts Claude SDK stream events to unified stream events.
Message persistence is handled by ChatMessage with raw JSON - no conversion needed.

Note: This adapter requires the claude-agent-sdk package to be installed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk.types import StreamEvent

from tracecat.agent.adapter.base import BaseHarnessAdapter
from tracecat.agent.stream.types import (
    HarnessType,
    StreamEventType,
    UnifiedStreamEvent,
)


@dataclass(slots=True, kw_only=True)
class _BlockState:
    """Tracks state for a content block between start and stop events."""

    block_type: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    args_json: str = ""


class ClaudeSDKAdapter(BaseHarnessAdapter):
    """Adapter for converting Claude SDK stream events to unified format."""

    harness_name = HarnessType.CLAUDE

    def __init__(self) -> None:
        self.context: dict[int, _BlockState] = {}

    def to_unified_event(self, native: StreamEvent) -> UnifiedStreamEvent:
        """Convert a Claude SDK StreamEvent to UnifiedStreamEvent."""
        # Handle dict-style events
        if isinstance(native, dict):
            event_data = native
        else:
            event_data = getattr(native, "event", native)

        if not isinstance(event_data, dict):
            return UnifiedStreamEvent(type=StreamEventType.MESSAGE_START)

        event_type = event_data.get("type")

        if event_type == "content_block_start":
            return self._convert_content_block_start(event_data)
        elif event_type == "content_block_delta":
            return self._convert_content_block_delta(event_data)
        elif event_type == "content_block_stop":
            return self._convert_content_block_stop(event_data)
        elif event_type == "message_start":
            return UnifiedStreamEvent(type=StreamEventType.MESSAGE_START)
        elif event_type == "message_stop":
            return UnifiedStreamEvent(type=StreamEventType.MESSAGE_STOP)
        else:
            return UnifiedStreamEvent(type=StreamEventType.MESSAGE_START)

    def _convert_content_block_start(
        self, event_data: dict[str, Any]
    ) -> UnifiedStreamEvent:
        """Convert content_block_start event."""
        index: int = event_data.get("index", 0)
        content_block = event_data.get("content_block", {})
        block_type = content_block.get("type")

        # Store block metadata in context for stop event
        self.context[index] = _BlockState(
            block_type=block_type or "unknown",
            tool_call_id=content_block.get("id"),
            tool_name=content_block.get("name"),
        )

        if block_type == "text":
            return UnifiedStreamEvent(
                type=StreamEventType.TEXT_START,
                part_id=index,
            )
        elif block_type == "thinking":
            return UnifiedStreamEvent(
                type=StreamEventType.THINKING_START,
                part_id=index,
            )
        elif block_type == "tool_use":
            # Claude API always provides an id for tool_use blocks
            tool_call_id = content_block.get("id")
            if tool_call_id is None:
                raise ValueError("Claude tool_use block missing required 'id' field")
            return UnifiedStreamEvent(
                type=StreamEventType.TOOL_CALL_START,
                part_id=index,
                tool_call_id=tool_call_id,
                tool_name=content_block.get("name", "unknown"),
                tool_input={},
            )
        else:
            return UnifiedStreamEvent(
                type=StreamEventType.MESSAGE_START,
                part_id=index,
            )

    def _convert_content_block_delta(
        self, event_data: dict[str, Any]
    ) -> UnifiedStreamEvent:
        """Convert content_block_delta event."""
        index: int = event_data.get("index", 0)
        delta = event_data.get("delta", {})
        delta_type = delta.get("type")

        if delta_type == "text_delta":
            return UnifiedStreamEvent(
                type=StreamEventType.TEXT_DELTA,
                part_id=index,
                text=delta.get("text", ""),
            )
        elif delta_type == "thinking_delta":
            return UnifiedStreamEvent(
                type=StreamEventType.THINKING_DELTA,
                part_id=index,
                thinking=delta.get("thinking", ""),
            )
        elif delta_type == "input_json_delta":
            partial_json = delta.get("partial_json", "")
            # Accumulate partial JSON in context for stop event
            if index in self.context:
                self.context[index].args_json += partial_json
            return UnifiedStreamEvent(
                type=StreamEventType.TOOL_CALL_DELTA,
                part_id=index,
                text=partial_json,
            )
        else:
            return UnifiedStreamEvent(
                type=StreamEventType.TEXT_DELTA,
                part_id=index,
            )

    def _convert_content_block_stop(
        self, event_data: dict[str, Any]
    ) -> UnifiedStreamEvent:
        """Convert content_block_stop event."""
        index: int = event_data.get("index", 0)

        # Retrieve and remove block metadata from context
        state = self.context.pop(index, None)
        block_type = state.block_type if state else "text"

        if block_type == "thinking":
            return UnifiedStreamEvent(
                type=StreamEventType.THINKING_STOP,
                part_id=index,
            )
        elif block_type == "tool_use":
            # Parse accumulated JSON args
            args_json = state.args_json if state else ""
            try:
                args = json.loads(args_json) if args_json else {}
            except json.JSONDecodeError:
                args = {}

            # tool_call_id was stored from content_block_start - should always exist
            tool_call_id = state.tool_call_id if state else None
            if tool_call_id is None:
                raise ValueError(
                    "Missing tool_call_id in context for tool_use stop event"
                )
            return UnifiedStreamEvent(
                type=StreamEventType.TOOL_CALL_STOP,
                part_id=index,
                tool_call_id=tool_call_id,
                tool_name=(state.tool_name if state else None) or "unknown",
                tool_input=args,
            )
        else:
            return UnifiedStreamEvent(
                type=StreamEventType.TEXT_STOP,
                part_id=index,
            )
