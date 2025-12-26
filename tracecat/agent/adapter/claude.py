"""Claude Agent SDK harness adapter.

Converts Claude SDK stream events to unified stream events.
Message persistence is handled by ChatMessage with raw JSON - no conversion needed.

Note: This adapter requires the claude-agent-sdk package to be installed.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from tracecat.agent.adapter.base import BaseHarnessAdapter
from tracecat.agent.stream.types import (
    HarnessType,
    StreamEventType,
    UnifiedStreamEvent,
)


class ClaudeSDKAdapter(BaseHarnessAdapter):
    """Adapter for converting Claude SDK stream events to unified format."""

    @classmethod
    def harness_name(cls) -> HarnessType:
        return HarnessType.CLAUDE

    @classmethod
    def to_unified_event(
        cls, native: Any, context: dict[int, Any] | None = None
    ) -> UnifiedStreamEvent:
        """Convert a Claude SDK StreamEvent to UnifiedStreamEvent.

        Args:
            native: The Claude SDK stream event
            context: Optional dict for tracking state across events in a single stream.
                     Caller should create one dict per stream and pass it to all calls.
                     Required for proper tool call handling - accumulates partial JSON.
        """
        # Use provided context or empty dict (stateless fallback)
        ctx = context if context is not None else {}

        # Handle dict-style events
        if isinstance(native, dict):
            event_data = native
        else:
            event_data = getattr(native, "event", native)

        if not isinstance(event_data, dict):
            return UnifiedStreamEvent(type=StreamEventType.MESSAGE_START)

        event_type = event_data.get("type")

        if event_type == "content_block_start":
            return cls._convert_content_block_start(event_data, ctx)
        elif event_type == "content_block_delta":
            return cls._convert_content_block_delta(event_data, ctx)
        elif event_type == "content_block_stop":
            return cls._convert_content_block_stop(event_data, ctx)
        elif event_type == "message_start":
            return UnifiedStreamEvent(type=StreamEventType.MESSAGE_START)
        elif event_type == "message_stop":
            return UnifiedStreamEvent(type=StreamEventType.MESSAGE_STOP)
        else:
            return UnifiedStreamEvent(type=StreamEventType.MESSAGE_START)

    @classmethod
    def _convert_content_block_start(
        cls, event_data: dict[str, Any], context: dict[int, Any]
    ) -> UnifiedStreamEvent:
        """Convert content_block_start event."""
        index: int = event_data.get("index", 0)
        content_block = event_data.get("content_block", {})
        block_type = content_block.get("type")

        # Store block metadata in context for stop event
        context[index] = {
            "block_type": block_type,
            "tool_id": content_block.get("id"),
            "tool_name": content_block.get("name"),
            "args_json": "",
        }

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
            return UnifiedStreamEvent(
                type=StreamEventType.TOOL_CALL_START,
                part_id=index,
                tool_call_id=content_block.get("id", str(uuid.uuid4())),
                tool_name=content_block.get("name", "unknown"),
                tool_input={},
            )
        else:
            return UnifiedStreamEvent(
                type=StreamEventType.MESSAGE_START,
                part_id=index,
            )

    @classmethod
    def _convert_content_block_delta(
        cls, event_data: dict[str, Any], context: dict[int, Any]
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
            if index in context:
                context[index]["args_json"] += partial_json
            return UnifiedStreamEvent(
                type=StreamEventType.TOOL_CALL_DELTA,
                part_id=index,
                text=partial_json,  # Use text field for delta content
            )
        else:
            return UnifiedStreamEvent(
                type=StreamEventType.TEXT_DELTA,
                part_id=index,
            )

    @classmethod
    def _convert_content_block_stop(
        cls, event_data: dict[str, Any], context: dict[int, Any]
    ) -> UnifiedStreamEvent:
        """Convert content_block_stop event."""
        index: int = event_data.get("index", 0)

        # Retrieve and remove block metadata from context
        state = context.pop(index, {})
        block_type = state.get("block_type", "text")

        if block_type == "thinking":
            return UnifiedStreamEvent(
                type=StreamEventType.THINKING_STOP,
                part_id=index,
            )
        elif block_type == "tool_use":
            # Parse accumulated JSON args
            args_json = state.get("args_json", "")
            try:
                args = json.loads(args_json) if args_json else {}
            except json.JSONDecodeError:
                args = {}

            return UnifiedStreamEvent(
                type=StreamEventType.TOOL_CALL_STOP,
                part_id=index,
                tool_call_id=state.get("tool_id") or str(uuid.uuid4()),
                tool_name=state.get("tool_name") or "unknown",
                tool_input=args,
            )
        else:
            return UnifiedStreamEvent(
                type=StreamEventType.TEXT_STOP,
                part_id=index,
            )
