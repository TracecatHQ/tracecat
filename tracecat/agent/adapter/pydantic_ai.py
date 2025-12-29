"""PydanticAI harness adapter.

Converts pydantic-ai stream events to unified stream events.
Message persistence is handled by ChatMessage with raw JSON - no conversion needed.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
)

from tracecat.agent.adapter.base import BaseHarnessAdapter
from tracecat.agent.stream.types import (
    HarnessType,
    StreamEventType,
    UnifiedStreamEvent,
)


class PydanticAIAdapter(BaseHarnessAdapter):
    """Adapter for converting PydanticAI stream events to unified format."""

    harness_name = HarnessType.PYDANTIC_AI

    def to_unified_event(self, native: AgentStreamEvent) -> UnifiedStreamEvent:
        """Convert a pydantic-ai AgentStreamEvent to UnifiedStreamEvent."""
        if isinstance(native, PartStartEvent):
            return self._convert_part_start(native)
        elif isinstance(native, PartDeltaEvent):
            return self._convert_part_delta(native)
        elif isinstance(native, FunctionToolCallEvent):
            return self._convert_tool_call(native)
        elif isinstance(native, FunctionToolResultEvent):
            return self._convert_tool_result(native)
        else:
            # Unknown event type - return a generic event
            return UnifiedStreamEvent(type=StreamEventType.MESSAGE_START)

    def _convert_part_start(self, event: PartStartEvent) -> UnifiedStreamEvent:
        """Convert PartStartEvent to UnifiedStreamEvent."""
        part = event.part
        part_id = event.index

        if isinstance(part, TextPart):
            return UnifiedStreamEvent(
                type=StreamEventType.TEXT_START,
                part_id=part_id,
                text=part.content,
            )
        elif isinstance(part, ThinkingPart):
            return UnifiedStreamEvent(
                type=StreamEventType.THINKING_START,
                part_id=part_id,
                thinking=part.content,
            )
        elif isinstance(part, ToolCallPart):
            return UnifiedStreamEvent(
                type=StreamEventType.TOOL_CALL_START,
                part_id=part_id,
                tool_call_id=part.tool_call_id,
                tool_name=part.tool_name,
                tool_input=part.args_as_dict() if hasattr(part, "args_as_dict") else {},
            )
        else:
            return UnifiedStreamEvent(
                type=StreamEventType.MESSAGE_START,
                part_id=part_id,
            )

    def _convert_part_delta(self, event: PartDeltaEvent) -> UnifiedStreamEvent:
        """Convert PartDeltaEvent to UnifiedStreamEvent."""
        delta = event.delta
        part_id = event.index

        if isinstance(delta, TextPartDelta):
            return UnifiedStreamEvent(
                type=StreamEventType.TEXT_DELTA,
                part_id=part_id,
                text=delta.content_delta,
            )
        elif isinstance(delta, ThinkingPartDelta):
            return UnifiedStreamEvent(
                type=StreamEventType.THINKING_DELTA,
                part_id=part_id,
                thinking=delta.content_delta,
            )
        elif isinstance(delta, ToolCallPartDelta):
            # For tool call deltas, the args_delta could be str or dict
            args_text = (
                delta.args_delta
                if isinstance(delta.args_delta, str)
                else str(delta.args_delta)
                if delta.args_delta
                else None
            )
            return UnifiedStreamEvent(
                type=StreamEventType.TOOL_CALL_DELTA,
                part_id=part_id,
                text=args_text,
            )
        else:
            return UnifiedStreamEvent(
                type=StreamEventType.TEXT_DELTA,
                part_id=part_id,
            )

    def _convert_tool_call(self, event: FunctionToolCallEvent) -> UnifiedStreamEvent:
        """Convert FunctionToolCallEvent to UnifiedStreamEvent."""
        return UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_STOP,
            tool_call_id=event.part.tool_call_id,
            tool_name=event.part.tool_name,
            tool_input=event.part.args_as_dict()
            if hasattr(event.part, "args_as_dict")
            else {},
        )

    def _convert_tool_result(
        self, event: FunctionToolResultEvent
    ) -> UnifiedStreamEvent:
        """Convert FunctionToolResultEvent to UnifiedStreamEvent."""
        result = event.result
        is_error = isinstance(result, RetryPromptPart)

        if isinstance(result, ToolReturnPart):
            return UnifiedStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                tool_call_id=result.tool_call_id,
                tool_name=result.tool_name,
                tool_output=result.content,
                is_error=False,
            )
        elif isinstance(result, RetryPromptPart):
            return UnifiedStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                tool_call_id=result.tool_call_id,
                tool_name=result.tool_name,
                tool_output=result.content,
                is_error=True,
            )
        else:
            return UnifiedStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                tool_output=str(result),
                is_error=is_error,
            )
