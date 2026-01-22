"""PydanticAI harness adapter.

Converts pydantic-ai stream events to unified stream events.
Also provides conversion functions between Tracecat's harness-agnostic
deferred tool types and pydantic-ai's internal types.

Message persistence is handled by ChatMessage with raw JSON - no conversion needed.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai._function_schema import FunctionSchema
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
from pydantic_ai.tools import (
    DeferredToolRequests as PADeferredToolRequests,
)
from pydantic_ai.tools import (
    DeferredToolResults as PADeferredToolResults,
)
from pydantic_ai.tools import Tool as PATool
from pydantic_ai.tools import ToolApproved as PAToolApproved
from pydantic_ai.tools import ToolDenied as PAToolDenied
from pydantic_core import SchemaValidator, core_schema

from tracecat.agent.common.adapter_base import BaseHarnessAdapter
from tracecat.agent.common.stream_types import (
    HarnessType,
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.tools import call_tracecat_action
from tracecat.agent.types import (
    DeferredToolRequests,
    DeferredToolResults,
    Tool,
    ToolApproved,
    ToolDenied,
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


# --- Deferred Tool Type Conversion Functions ---


def from_pydantic_ai_deferred_requests(
    pa_requests: PADeferredToolRequests,
) -> DeferredToolRequests:
    """Convert pydantic-ai DeferredToolRequests to Tracecat's harness-agnostic type."""
    return DeferredToolRequests(
        approvals=[
            ToolCallContent(
                id=call.tool_call_id,
                name=call.tool_name,
                input=call.args_as_dict(),
            )
            for call in pa_requests.approvals
        ],
        calls=[
            ToolCallContent(
                id=call.tool_call_id,
                name=call.tool_name,
                input=call.args_as_dict(),
            )
            for call in pa_requests.calls
        ],
        metadata=pa_requests.metadata,
    )


def to_pydantic_ai_deferred_results(
    results: DeferredToolResults,
) -> PADeferredToolResults:
    """Convert Tracecat's DeferredToolResults to pydantic-ai's type for agent.run()."""
    pa_approvals: dict[str, bool | PAToolApproved | PAToolDenied] = {}
    for tool_call_id, approval in results.approvals.items():
        if isinstance(approval, bool):
            pa_approvals[tool_call_id] = approval
        elif isinstance(approval, ToolApproved):
            pa_approvals[tool_call_id] = PAToolApproved(
                override_args=approval.override_args
            )
        elif isinstance(approval, ToolDenied):
            pa_approvals[tool_call_id] = PAToolDenied(message=approval.message)
        else:
            # Fallback for raw dict or unexpected types
            pa_approvals[tool_call_id] = approval

    return PADeferredToolResults(
        approvals=pa_approvals,
        calls=results.calls,
    )


# --- Tool Conversion Functions ---


def to_pydantic_ai_tool(tool: Tool) -> PATool[Any]:
    """Convert a Tracecat Tool to a pydantic-ai Tool.

    Creates a callable wrapper function that executes the action via subprocess,
    with the function name using MCP format (underscores) as required by pydantic-ai.

    Args:
        tool: Tracecat's harness-agnostic Tool with canonical name

    Returns:
        A pydantic-ai Tool instance ready for use with Agent
    """
    # Capture the canonical action name for the closure
    action_name = tool.name

    # Create wrapper function that calls the action
    async def tool_func(**kwargs: Any) -> Any:
        return await call_tracecat_action(action_name, kwargs)

    # Set function name to MCP format (pydantic-ai requires valid Python identifier)
    tool_func.__name__ = action_name.replace(".", "__")
    tool_func.__doc__ = tool.description

    # Pass schema so LLM knows what parameters to provide (MCP server validates)
    return PATool(
        tool_func,
        requires_approval=tool.requires_approval,
        function_schema=FunctionSchema(
            function=tool_func,
            description=tool.description,
            validator=SchemaValidator(core_schema.any_schema()),
            json_schema=tool.parameters_json_schema,
            takes_ctx=False,
            is_async=True,
        ),
    )


def to_pydantic_ai_tools(tools: list[Tool]) -> list[PATool]:
    """Convert a list of Tracecat Tools to pydantic-ai Tools.

    Args:
        tools: List of Tracecat's harness-agnostic Tools

    Returns:
        List of pydantic-ai Tool instances
    """
    return [to_pydantic_ai_tool(tool) for tool in tools]
