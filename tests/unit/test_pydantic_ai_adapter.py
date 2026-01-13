"""Unit tests for PydanticAI adapter.

Tests the conversion from pydantic-ai AgentStreamEvent to UnifiedStreamEvent.
"""

import pytest
from pydantic_ai.messages import (
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

from tracecat.agent.runtime.pydantic_ai.adapter import PydanticAIAdapter
from tracecat.agent.shared.stream_types import HarnessType, StreamEventType

# ==============================================================================
# Basic Adapter Properties
# ==============================================================================


def test_harness_name():
    """Test that adapter returns correct harness name."""
    assert PydanticAIAdapter.harness_name == HarnessType.PYDANTIC_AI


# ==============================================================================
# Text Part Conversion Tests
# ==============================================================================


def test_text_part_start_conversion():
    """Test TextPart start event conversion."""
    native = PartStartEvent(index=0, part=TextPart(content="hello world"))
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TEXT_START
    assert unified.part_id == 0
    assert unified.text == "hello world"


def test_text_part_start_with_empty_content():
    """Test TextPart start with empty content."""
    native = PartStartEvent(index=0, part=TextPart(content=""))
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TEXT_START
    assert unified.part_id == 0
    assert unified.text == ""


def test_text_part_delta_conversion():
    """Test TextPartDelta conversion."""
    native = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" world"))
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TEXT_DELTA
    assert unified.part_id == 0
    assert unified.text == " world"


# ==============================================================================
# Thinking/Reasoning Part Conversion Tests
# ==============================================================================


def test_thinking_part_start_conversion():
    """Test ThinkingPart start event conversion."""
    native = PartStartEvent(index=0, part=ThinkingPart(content="analyzing..."))
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.THINKING_START
    assert unified.part_id == 0
    assert unified.thinking == "analyzing..."


def test_thinking_part_start_with_empty_content():
    """Test ThinkingPart start with empty content."""
    native = PartStartEvent(index=0, part=ThinkingPart(content=""))
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.THINKING_START
    assert unified.part_id == 0
    assert unified.thinking == ""


def test_thinking_part_delta_conversion():
    """Test ThinkingPartDelta conversion."""
    native = PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=", step 2"))
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.THINKING_DELTA
    assert unified.part_id == 0
    assert unified.thinking == ", step 2"


def test_thinking_part_delta_with_none_content():
    """Test ThinkingPartDelta with None content delta."""
    native = PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=None))
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.THINKING_DELTA
    assert unified.part_id == 0
    assert unified.thinking is None


# ==============================================================================
# Tool Call Part Conversion Tests
# ==============================================================================


def test_tool_call_start_conversion():
    """Test ToolCallPart start event conversion."""
    native = PartStartEvent(
        index=0,
        part=ToolCallPart(
            tool_name="search",
            tool_call_id="call_123",
            args={"query": "test"},
        ),
    )
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_CALL_START
    assert unified.part_id == 0
    assert unified.tool_call_id == "call_123"
    assert unified.tool_name == "search"
    assert unified.tool_input == {"query": "test"}


def test_tool_call_start_with_empty_args():
    """Test ToolCallPart start with empty args."""
    native = PartStartEvent(
        index=0,
        part=ToolCallPart(
            tool_name="get_time",
            tool_call_id="call_456",
            args={},
        ),
    )
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_CALL_START
    assert unified.part_id == 0
    assert unified.tool_call_id == "call_456"
    assert unified.tool_name == "get_time"
    assert unified.tool_input == {}


def test_tool_call_delta_conversion():
    """Test ToolCallPartDelta conversion."""
    native = PartDeltaEvent(
        index=0,
        delta=ToolCallPartDelta(
            tool_call_id="call_123",
            args_delta='{"extra": "data"}',
        ),
    )
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_CALL_DELTA
    assert unified.part_id == 0
    # Args delta is stored in text field
    assert unified.text == '{"extra": "data"}'


def test_tool_call_delta_with_none_args():
    """Test ToolCallPartDelta with None args_delta."""
    native = PartDeltaEvent(
        index=0,
        delta=ToolCallPartDelta(
            tool_call_id="call_123",
            args_delta=None,
        ),
    )
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_CALL_DELTA
    assert unified.part_id == 0
    assert unified.text is None


# ==============================================================================
# Function Tool Call Event Conversion Tests
# ==============================================================================


def test_function_tool_call_event_conversion():
    """Test FunctionToolCallEvent conversion to TOOL_CALL_STOP."""
    native = FunctionToolCallEvent(
        part=ToolCallPart(
            tool_name="search",
            tool_call_id="call_123",
            args={"query": "test", "limit": 10},
        )
    )
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_CALL_STOP
    assert unified.tool_call_id == "call_123"
    assert unified.tool_name == "search"
    assert unified.tool_input == {"query": "test", "limit": 10}


# ==============================================================================
# Tool Result Conversion Tests
# ==============================================================================


def test_tool_return_part_success():
    """Test ToolReturnPart conversion (success case)."""
    native = FunctionToolResultEvent(
        result=ToolReturnPart(
            tool_name="search",
            tool_call_id="call_123",
            content={"results": ["item1", "item2"]},
        )
    )
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_RESULT
    assert unified.tool_call_id == "call_123"
    assert unified.tool_name == "search"
    assert unified.is_error is False
    assert unified.tool_output is not None
    assert unified.tool_output["results"] == ["item1", "item2"]


def test_tool_return_part_with_string_content():
    """Test ToolReturnPart with string content."""
    native = FunctionToolResultEvent(
        result=ToolReturnPart(
            tool_name="search",
            tool_call_id="call_123",
            content="plain text result",
        )
    )
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_RESULT
    assert unified.tool_output == "plain text result"
    assert unified.is_error is False


def test_retry_prompt_part_error():
    """Test RetryPromptPart conversion (error case)."""
    native = FunctionToolResultEvent(
        result=RetryPromptPart(
            tool_name="search",
            tool_call_id="call_123",
            content="Validation error: query too short",
        )
    )
    unified = PydanticAIAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_RESULT
    assert unified.tool_name == "search"
    assert unified.is_error is True
    assert unified.tool_output is not None
    assert "Validation error" in unified.tool_output


# ==============================================================================
# Unknown Event Type Conversion Tests
# ==============================================================================


def test_unknown_event_type_fallback():
    """Test unknown event type returns MESSAGE_START fallback."""

    # Create a mock event that doesn't match any known type
    class UnknownEvent:
        pass

    native = UnknownEvent()
    unified = PydanticAIAdapter().to_unified_event(native)  # type: ignore[arg-type]

    assert unified.type == StreamEventType.MESSAGE_START


# ==============================================================================
# Part Index Preservation Tests
# ==============================================================================


@pytest.mark.parametrize("index", [0, 1, 5, 10, 100])
def test_part_index_preserved_for_text(index: int):
    """Test that part_id is preserved across different indices."""
    native = PartStartEvent(index=index, part=TextPart(content="test"))
    unified = PydanticAIAdapter().to_unified_event(native)
    assert unified.part_id == index


@pytest.mark.parametrize("index", [0, 1, 5, 10, 100])
def test_part_index_preserved_for_thinking(index: int):
    """Test that part_id is preserved for thinking parts."""
    native = PartStartEvent(index=index, part=ThinkingPart(content="test"))
    unified = PydanticAIAdapter().to_unified_event(native)
    assert unified.part_id == index


@pytest.mark.parametrize("index", [0, 1, 5, 10, 100])
def test_part_index_preserved_for_delta(index: int):
    """Test that part_id is preserved for delta events."""
    native = PartDeltaEvent(index=index, delta=TextPartDelta(content_delta="delta"))
    unified = PydanticAIAdapter().to_unified_event(native)
    assert unified.part_id == index
