"""Unit tests for Claude SDK adapter.

Tests the conversion from Claude SDK StreamEvent to UnifiedStreamEvent.
"""

from collections.abc import Mapping
from typing import Any

import pytest
from claude_agent_sdk.types import StreamEvent

from tracecat.agent.adapter.claude import (
    ClaudeSDKAdapter,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    InputJsonDelta,
    TextContentBlock,
    TextDelta,
    ThinkingContentBlock,
    ThinkingDelta,
    ToolUseContentBlock,
)
from tracecat.agent.stream.types import HarnessType, StreamEventType

# ==============================================================================
# Helper to create StreamEvent objects
# ==============================================================================


def make_stream_event(
    event: Mapping[str, Any], session_id: str = "sess_123"
) -> StreamEvent:
    """Create a StreamEvent with the given raw event dict."""
    return StreamEvent(
        uuid="evt_123",
        session_id=session_id,
        event=dict(event),
        parent_tool_use_id=None,
    )


# ==============================================================================
# Basic Adapter Properties
# ==============================================================================


def test_harness_name():
    """Test that adapter returns correct harness name."""
    assert ClaudeSDKAdapter().harness_name == HarnessType.CLAUDE


# ==============================================================================
# Message Lifecycle Events
# ==============================================================================


def test_message_start_event():
    """Test message_start event conversion."""
    native = make_stream_event({"type": "message_start", "message": {"id": "msg_123"}})
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.MESSAGE_START


def test_message_stop_event():
    """Test message_stop event conversion."""
    native = make_stream_event({"type": "message_stop"})
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.MESSAGE_STOP


# ==============================================================================
# Text Content Block Events
# ==============================================================================


def test_text_block_start():
    """Test text content_block_start event conversion."""
    native = make_stream_event(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }
    )
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TEXT_START
    assert unified.part_id == 0


def test_text_block_delta():
    """Test text_delta event conversion."""
    native = make_stream_event(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello, world!"},
        }
    )
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TEXT_DELTA
    assert unified.part_id == 0
    assert unified.text == "Hello, world!"


def test_text_block_delta_empty():
    """Test text_delta with empty text."""
    native = make_stream_event(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": ""},
        }
    )
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TEXT_DELTA
    assert unified.text == ""


def test_text_block_stop():
    """Test text content_block_stop event conversion."""
    native = make_stream_event({"type": "content_block_stop", "index": 0})
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TEXT_STOP
    assert unified.part_id == 0


# ==============================================================================
# Thinking Content Block Events
# ==============================================================================


def test_thinking_block_start():
    """Test thinking content_block_start event conversion."""
    content_block: ThinkingContentBlock = {
        "type": "thinking",
        "thinking": "",
        "signature": "",
    }
    event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 0,
        "content_block": content_block,
    }
    native = make_stream_event(event)
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.THINKING_START
    assert unified.part_id == 0


def test_thinking_block_delta():
    """Test thinking_delta event conversion."""
    delta: ThinkingDelta = {"type": "thinking_delta", "thinking": "Let me analyze..."}
    event: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 0,
        "delta": delta,
    }
    native = make_stream_event(event)
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.THINKING_DELTA
    assert unified.part_id == 0
    assert unified.thinking == "Let me analyze..."


def test_thinking_block_delta_empty():
    """Test thinking_delta with empty thinking."""
    delta: ThinkingDelta = {"type": "thinking_delta", "thinking": ""}
    event: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 0,
        "delta": delta,
    }
    native = make_stream_event(event)
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.THINKING_DELTA
    assert unified.thinking == ""


def test_thinking_block_stop():
    """Test thinking content_block_stop event conversion."""
    adapter = ClaudeSDKAdapter()
    # Start the thinking block first
    content_block: ThinkingContentBlock = {
        "type": "thinking",
        "thinking": "",
        "signature": "",
    }
    start_event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 0,
        "content_block": content_block,
    }
    adapter.to_unified_event(make_stream_event(start_event))
    # Stop it
    stop_event: ContentBlockStopEvent = {"type": "content_block_stop", "index": 0}
    native = make_stream_event(stop_event)
    unified = adapter.to_unified_event(native)

    assert unified.type == StreamEventType.THINKING_STOP
    assert unified.part_id == 0


# ==============================================================================
# Tool Use Content Block Events
# ==============================================================================


def test_tool_use_block_start():
    """Test tool_use content_block_start event conversion."""
    native = make_stream_event(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_123",
                "name": "search",
                "input": {},
            },
        }
    )
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_CALL_START
    assert unified.part_id == 0
    assert unified.tool_call_id == "toolu_123"
    assert unified.tool_name == "search"
    assert unified.tool_input == {}


def test_tool_use_input_json_delta():
    """Test input_json_delta event conversion."""
    adapter = ClaudeSDKAdapter()
    # Start the tool use block first
    content_block: ToolUseContentBlock = {
        "type": "tool_use",
        "id": "toolu_123",
        "name": "search",
        "input": {},
    }
    start_event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 0,
        "content_block": content_block,
    }
    adapter.to_unified_event(make_stream_event(start_event))
    delta: InputJsonDelta = {
        "type": "input_json_delta",
        "partial_json": '{"query": "test"',
    }
    delta_event: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 0,
        "delta": delta,
    }
    native = make_stream_event(delta_event)
    unified = adapter.to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_CALL_DELTA
    assert unified.part_id == 0
    assert unified.text == '{"query": "test"'
    # Verify context accumulates
    assert adapter.context[0].args_json == '{"query": "test"'


def test_tool_use_input_json_delta_accumulation():
    """Test that input_json_delta accumulates in context."""
    adapter = ClaudeSDKAdapter()
    # Start the tool use block
    content_block: ToolUseContentBlock = {
        "type": "tool_use",
        "id": "toolu_123",
        "name": "search",
        "input": {},
    }
    start_event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 0,
        "content_block": content_block,
    }
    adapter.to_unified_event(make_stream_event(start_event))
    # First delta
    delta1: InputJsonDelta = {"type": "input_json_delta", "partial_json": '{"query": '}
    delta_event1: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 0,
        "delta": delta1,
    }
    adapter.to_unified_event(make_stream_event(delta_event1))
    # Second delta
    delta2: InputJsonDelta = {"type": "input_json_delta", "partial_json": '"hello"}'}
    delta_event2: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 0,
        "delta": delta2,
    }
    adapter.to_unified_event(make_stream_event(delta_event2))

    assert adapter.context[0].args_json == '{"query": "hello"}'


def test_tool_use_block_stop():
    """Test tool_use content_block_stop event conversion."""
    adapter = ClaudeSDKAdapter()
    # Start the tool use block
    content_block: ToolUseContentBlock = {
        "type": "tool_use",
        "id": "toolu_123",
        "name": "search",
        "input": {},
    }
    start_event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 0,
        "content_block": content_block,
    }
    adapter.to_unified_event(make_stream_event(start_event))
    # Send the JSON args
    delta: InputJsonDelta = {
        "type": "input_json_delta",
        "partial_json": '{"query": "test", "limit": 10}',
    }
    delta_event: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 0,
        "delta": delta,
    }
    adapter.to_unified_event(make_stream_event(delta_event))
    # Stop the block
    stop_event: ContentBlockStopEvent = {"type": "content_block_stop", "index": 0}
    native = make_stream_event(stop_event)
    unified = adapter.to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_CALL_STOP
    assert unified.part_id == 0
    assert unified.tool_call_id == "toolu_123"
    assert unified.tool_name == "search"
    assert unified.tool_input == {"query": "test", "limit": 10}


def test_tool_use_block_stop_empty_args():
    """Test tool_use stop with empty args JSON."""
    adapter = ClaudeSDKAdapter()
    # Start the tool use block (no delta, so args stay empty)
    content_block: ToolUseContentBlock = {
        "type": "tool_use",
        "id": "toolu_456",
        "name": "get_time",
        "input": {},
    }
    start_event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 0,
        "content_block": content_block,
    }
    adapter.to_unified_event(make_stream_event(start_event))
    stop_event: ContentBlockStopEvent = {"type": "content_block_stop", "index": 0}
    native = make_stream_event(stop_event)
    unified = adapter.to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_CALL_STOP
    assert unified.tool_input == {}


def test_tool_use_block_stop_invalid_json():
    """Test tool_use stop with invalid JSON falls back to empty dict."""
    adapter = ClaudeSDKAdapter()
    # Start the tool use block
    content_block: ToolUseContentBlock = {
        "type": "tool_use",
        "id": "toolu_789",
        "name": "broken",
        "input": {},
    }
    start_event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 0,
        "content_block": content_block,
    }
    adapter.to_unified_event(make_stream_event(start_event))
    # Send incomplete JSON
    delta: InputJsonDelta = {
        "type": "input_json_delta",
        "partial_json": '{"incomplete": ',
    }
    delta_event: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 0,
        "delta": delta,
    }
    adapter.to_unified_event(make_stream_event(delta_event))
    stop_event: ContentBlockStopEvent = {"type": "content_block_stop", "index": 0}
    native = make_stream_event(stop_event)
    unified = adapter.to_unified_event(native)

    assert unified.type == StreamEventType.TOOL_CALL_STOP
    assert unified.tool_input == {}


# ==============================================================================
# Context Management Tests
# ==============================================================================


def test_context_populated_on_block_start():
    """Test that context is populated on content_block_start."""
    adapter = ClaudeSDKAdapter()
    content_block: ToolUseContentBlock = {
        "type": "tool_use",
        "id": "toolu_abc",
        "name": "calc",
        "input": {},
    }
    event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 2,
        "content_block": content_block,
    }
    native = make_stream_event(event)
    adapter.to_unified_event(native)

    assert 2 in adapter.context
    assert adapter.context[2].block_type == "tool_use"
    assert adapter.context[2].tool_call_id == "toolu_abc"
    assert adapter.context[2].tool_name == "calc"
    assert adapter.context[2].args_json == ""


def test_context_cleared_on_block_stop():
    """Test that context entry is removed on content_block_stop."""
    adapter = ClaudeSDKAdapter()
    # Start a block first
    content_block: TextContentBlock = {"type": "text", "text": ""}
    start_event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 0,
        "content_block": content_block,
    }
    adapter.to_unified_event(make_stream_event(start_event))
    assert 0 in adapter.context
    # Stop it
    stop_event: ContentBlockStopEvent = {"type": "content_block_stop", "index": 0}
    native = make_stream_event(stop_event)
    adapter.to_unified_event(native)

    assert 0 not in adapter.context


def test_multiple_blocks_independent_context():
    """Test that multiple blocks maintain independent context."""
    adapter = ClaudeSDKAdapter()

    # Start two blocks
    text_block: TextContentBlock = {"type": "text", "text": ""}
    text_event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 0,
        "content_block": text_block,
    }
    adapter.to_unified_event(make_stream_event(text_event))

    tool_block: ToolUseContentBlock = {
        "type": "tool_use",
        "id": "t1",
        "name": "search",
        "input": {},
    }
    tool_event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 1,
        "content_block": tool_block,
    }
    adapter.to_unified_event(make_stream_event(tool_event))

    assert 0 in adapter.context
    assert 1 in adapter.context
    assert adapter.context[0].block_type == "text"
    assert adapter.context[1].block_type == "tool_use"


# ==============================================================================
# Unknown Event Type Handling
# ==============================================================================


def test_unknown_content_block_type_fallback():
    """Test unknown content block type returns MESSAGE_START."""
    native = make_stream_event(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "unknown_block_type"},
        }
    )
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.MESSAGE_START
    assert unified.part_id == 0


def test_unknown_delta_type_fallback():
    """Test unknown delta type returns TEXT_DELTA fallback."""
    native = make_stream_event(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "unknown_delta_type"},
        }
    )
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TEXT_DELTA
    assert unified.part_id == 0


# ==============================================================================
# Part Index Preservation Tests
# ==============================================================================


@pytest.mark.parametrize("index", [0, 1, 5, 10, 100])
def test_part_index_preserved_for_text_start(index: int):
    """Test that part_id is preserved across different indices for text start."""
    content_block: TextContentBlock = {"type": "text", "text": ""}
    event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": index,
        "content_block": content_block,
    }
    native = make_stream_event(event)
    unified = ClaudeSDKAdapter().to_unified_event(native)
    assert unified.part_id == index


@pytest.mark.parametrize("index", [0, 1, 5, 10, 100])
def test_part_index_preserved_for_delta(index: int):
    """Test that part_id is preserved for delta events."""
    delta: TextDelta = {"type": "text_delta", "text": "test"}
    event: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": index,
        "delta": delta,
    }
    native = make_stream_event(event)
    unified = ClaudeSDKAdapter().to_unified_event(native)
    assert unified.part_id == index


@pytest.mark.parametrize("index", [0, 1, 5, 10, 100])
def test_part_index_preserved_for_stop(index: int):
    """Test that part_id is preserved for stop events."""
    adapter = ClaudeSDKAdapter()
    # Start the block first
    content_block: TextContentBlock = {"type": "text", "text": ""}
    start_event: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": index,
        "content_block": content_block,
    }
    adapter.to_unified_event(make_stream_event(start_event))
    stop_event: ContentBlockStopEvent = {"type": "content_block_stop", "index": index}
    native = make_stream_event(stop_event)
    unified = adapter.to_unified_event(native)
    assert unified.part_id == index


# ==============================================================================
# Full Stream Sequence Tests
# ==============================================================================


def test_full_text_stream_sequence():
    """Test a complete text streaming sequence."""
    adapter = ClaudeSDKAdapter()
    text_block: TextContentBlock = {"type": "text", "text": ""}
    text_start: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 0,
        "content_block": text_block,
    }
    delta1: TextDelta = {"type": "text_delta", "text": "Hello"}
    delta_event1: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 0,
        "delta": delta1,
    }
    delta2: TextDelta = {"type": "text_delta", "text": ", world!"}
    delta_event2: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 0,
        "delta": delta2,
    }
    stop_event: ContentBlockStopEvent = {"type": "content_block_stop", "index": 0}
    events = [
        make_stream_event({"type": "message_start", "message": {"id": "msg_1"}}),
        make_stream_event(text_start),
        make_stream_event(delta_event1),
        make_stream_event(delta_event2),
        make_stream_event(stop_event),
        make_stream_event({"type": "message_stop"}),
    ]

    results = [adapter.to_unified_event(e) for e in events]

    assert results[0].type == StreamEventType.MESSAGE_START
    assert results[1].type == StreamEventType.TEXT_START
    assert results[2].type == StreamEventType.TEXT_DELTA
    assert results[2].text == "Hello"
    assert results[3].type == StreamEventType.TEXT_DELTA
    assert results[3].text == ", world!"
    assert results[4].type == StreamEventType.TEXT_STOP
    assert results[5].type == StreamEventType.MESSAGE_STOP


def test_full_tool_use_stream_sequence():
    """Test a complete tool use streaming sequence."""
    adapter = ClaudeSDKAdapter()
    tool_block: ToolUseContentBlock = {
        "type": "tool_use",
        "id": "toolu_xyz",
        "name": "search",
        "input": {},
    }
    tool_start: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 0,
        "content_block": tool_block,
    }
    delta1: InputJsonDelta = {"type": "input_json_delta", "partial_json": '{"query"'}
    delta_event1: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 0,
        "delta": delta1,
    }
    delta2: InputJsonDelta = {"type": "input_json_delta", "partial_json": ': "test"}'}
    delta_event2: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 0,
        "delta": delta2,
    }
    stop_event: ContentBlockStopEvent = {"type": "content_block_stop", "index": 0}
    events = [
        make_stream_event({"type": "message_start", "message": {"id": "msg_2"}}),
        make_stream_event(tool_start),
        make_stream_event(delta_event1),
        make_stream_event(delta_event2),
        make_stream_event(stop_event),
        make_stream_event({"type": "message_stop"}),
    ]

    results = [adapter.to_unified_event(e) for e in events]

    assert results[0].type == StreamEventType.MESSAGE_START
    assert results[1].type == StreamEventType.TOOL_CALL_START
    assert results[1].tool_name == "search"
    assert results[2].type == StreamEventType.TOOL_CALL_DELTA
    assert results[3].type == StreamEventType.TOOL_CALL_DELTA
    assert results[4].type == StreamEventType.TOOL_CALL_STOP
    assert results[4].tool_input == {"query": "test"}
    assert results[5].type == StreamEventType.MESSAGE_STOP


def test_mixed_content_stream_sequence():
    """Test a stream with thinking followed by text."""
    adapter = ClaudeSDKAdapter()
    # Thinking block events
    thinking_block: ThinkingContentBlock = {
        "type": "thinking",
        "thinking": "",
        "signature": "",
    }
    thinking_start: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 0,
        "content_block": thinking_block,
    }
    thinking_delta: ThinkingDelta = {
        "type": "thinking_delta",
        "thinking": "Let me think...",
    }
    thinking_delta_event: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 0,
        "delta": thinking_delta,
    }
    thinking_stop: ContentBlockStopEvent = {"type": "content_block_stop", "index": 0}
    # Text block events
    text_block: TextContentBlock = {"type": "text", "text": ""}
    text_start: ContentBlockStartEvent = {
        "type": "content_block_start",
        "index": 1,
        "content_block": text_block,
    }
    text_delta: TextDelta = {"type": "text_delta", "text": "The answer is 42."}
    text_delta_event: ContentBlockDeltaEvent = {
        "type": "content_block_delta",
        "index": 1,
        "delta": text_delta,
    }
    text_stop: ContentBlockStopEvent = {"type": "content_block_stop", "index": 1}
    events = [
        make_stream_event({"type": "message_start", "message": {"id": "msg_3"}}),
        make_stream_event(thinking_start),
        make_stream_event(thinking_delta_event),
        make_stream_event(thinking_stop),
        make_stream_event(text_start),
        make_stream_event(text_delta_event),
        make_stream_event(text_stop),
        make_stream_event({"type": "message_stop"}),
    ]

    results = [adapter.to_unified_event(e) for e in events]

    assert results[0].type == StreamEventType.MESSAGE_START
    assert results[1].type == StreamEventType.THINKING_START
    assert results[2].type == StreamEventType.THINKING_DELTA
    assert results[2].thinking == "Let me think..."
    assert results[3].type == StreamEventType.THINKING_STOP
    assert results[4].type == StreamEventType.TEXT_START
    assert results[5].type == StreamEventType.TEXT_DELTA
    assert results[5].text == "The answer is 42."
    assert results[6].type == StreamEventType.TEXT_STOP
    assert results[7].type == StreamEventType.MESSAGE_STOP


def test_stream_event_with_parent_tool_use_id():
    """Test that StreamEvent with parent_tool_use_id works correctly."""
    native = StreamEvent(
        uuid="evt_456",
        session_id="sess_123",
        event={
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Tool output text"},
        },
        parent_tool_use_id="toolu_parent",
    )
    unified = ClaudeSDKAdapter().to_unified_event(native)

    assert unified.type == StreamEventType.TEXT_DELTA
    assert unified.text == "Tool output text"
