"""Unit tests for Claude SDK adapter.

Tests the conversion from Claude SDK StreamEvent to UnifiedStreamEvent.
"""

import pytest
from claude_agent_sdk.types import StreamEvent

from tracecat.agent.adapter.claude import ClaudeSDKAdapter
from tracecat.agent.stream.types import HarnessType, StreamEventType

# ==============================================================================
# Helper to create StreamEvent objects
# ==============================================================================


def make_stream_event(event: dict, session_id: str = "sess_123") -> StreamEvent:
    """Create a StreamEvent with the given raw event dict."""
    return StreamEvent(
        uuid="evt_123",
        session_id=session_id,
        event=event,
        parent_tool_use_id=None,
    )


# ==============================================================================
# Basic Adapter Properties
# ==============================================================================


def test_harness_name():
    """Test that adapter returns correct harness name."""
    assert ClaudeSDKAdapter.harness_name() == HarnessType.CLAUDE


# ==============================================================================
# Message Lifecycle Events
# ==============================================================================


def test_message_start_event():
    """Test message_start event conversion."""
    native = make_stream_event({"type": "message_start", "message": {"id": "msg_123"}})
    unified = ClaudeSDKAdapter.to_unified_event(native)

    assert unified.type == StreamEventType.MESSAGE_START


def test_message_stop_event():
    """Test message_stop event conversion."""
    native = make_stream_event({"type": "message_stop"})
    unified = ClaudeSDKAdapter.to_unified_event(native)

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
    context: dict = {}
    unified = ClaudeSDKAdapter.to_unified_event(native, context)

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
    unified = ClaudeSDKAdapter.to_unified_event(native)

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
    unified = ClaudeSDKAdapter.to_unified_event(native)

    assert unified.type == StreamEventType.TEXT_DELTA
    assert unified.text == ""


def test_text_block_stop():
    """Test text content_block_stop event conversion."""
    context: dict = {0: {"block_type": "text"}}
    native = make_stream_event({"type": "content_block_stop", "index": 0})
    unified = ClaudeSDKAdapter.to_unified_event(native, context)

    assert unified.type == StreamEventType.TEXT_STOP
    assert unified.part_id == 0


# ==============================================================================
# Thinking Content Block Events
# ==============================================================================


def test_thinking_block_start():
    """Test thinking content_block_start event conversion."""
    native = make_stream_event(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        }
    )
    context: dict = {}
    unified = ClaudeSDKAdapter.to_unified_event(native, context)

    assert unified.type == StreamEventType.THINKING_START
    assert unified.part_id == 0


def test_thinking_block_delta():
    """Test thinking_delta event conversion."""
    native = make_stream_event(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Let me analyze..."},
        }
    )
    unified = ClaudeSDKAdapter.to_unified_event(native)

    assert unified.type == StreamEventType.THINKING_DELTA
    assert unified.part_id == 0
    assert unified.thinking == "Let me analyze..."


def test_thinking_block_delta_empty():
    """Test thinking_delta with empty thinking."""
    native = make_stream_event(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": ""},
        }
    )
    unified = ClaudeSDKAdapter.to_unified_event(native)

    assert unified.type == StreamEventType.THINKING_DELTA
    assert unified.thinking == ""


def test_thinking_block_stop():
    """Test thinking content_block_stop event conversion."""
    context: dict = {0: {"block_type": "thinking"}}
    native = make_stream_event({"type": "content_block_stop", "index": 0})
    unified = ClaudeSDKAdapter.to_unified_event(native, context)

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
    context: dict = {}
    unified = ClaudeSDKAdapter.to_unified_event(native, context)

    assert unified.type == StreamEventType.TOOL_CALL_START
    assert unified.part_id == 0
    assert unified.tool_call_id == "toolu_123"
    assert unified.tool_name == "search"
    assert unified.tool_input == {}


def test_tool_use_block_start_without_id():
    """Test tool_use start without id generates a UUID."""
    native = make_stream_event(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "name": "search"},
        }
    )
    context: dict = {}
    unified = ClaudeSDKAdapter.to_unified_event(native, context)

    assert unified.type == StreamEventType.TOOL_CALL_START
    assert unified.tool_call_id is not None
    assert len(unified.tool_call_id) > 0


def test_tool_use_input_json_delta():
    """Test input_json_delta event conversion."""
    native = make_stream_event(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"query": "test"'},
        }
    )
    context: dict = {0: {"block_type": "tool_use", "args_json": ""}}
    unified = ClaudeSDKAdapter.to_unified_event(native, context)

    assert unified.type == StreamEventType.TOOL_CALL_DELTA
    assert unified.part_id == 0
    assert unified.text == '{"query": "test"'
    # Verify context accumulates
    assert context[0]["args_json"] == '{"query": "test"'


def test_tool_use_input_json_delta_accumulation():
    """Test that input_json_delta accumulates in context."""
    context: dict = {0: {"block_type": "tool_use", "args_json": '{"query": '}}

    native = make_stream_event(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '"hello"}'},
        }
    )
    ClaudeSDKAdapter.to_unified_event(native, context)

    assert context[0]["args_json"] == '{"query": "hello"}'


def test_tool_use_block_stop():
    """Test tool_use content_block_stop event conversion."""
    context: dict = {
        0: {
            "block_type": "tool_use",
            "tool_id": "toolu_123",
            "tool_name": "search",
            "args_json": '{"query": "test", "limit": 10}',
        }
    }
    native = make_stream_event({"type": "content_block_stop", "index": 0})
    unified = ClaudeSDKAdapter.to_unified_event(native, context)

    assert unified.type == StreamEventType.TOOL_CALL_STOP
    assert unified.part_id == 0
    assert unified.tool_call_id == "toolu_123"
    assert unified.tool_name == "search"
    assert unified.tool_input == {"query": "test", "limit": 10}


def test_tool_use_block_stop_empty_args():
    """Test tool_use stop with empty args JSON."""
    context: dict = {
        0: {
            "block_type": "tool_use",
            "tool_id": "toolu_456",
            "tool_name": "get_time",
            "args_json": "",
        }
    }
    native = make_stream_event({"type": "content_block_stop", "index": 0})
    unified = ClaudeSDKAdapter.to_unified_event(native, context)

    assert unified.type == StreamEventType.TOOL_CALL_STOP
    assert unified.tool_input == {}


def test_tool_use_block_stop_invalid_json():
    """Test tool_use stop with invalid JSON falls back to empty dict."""
    context: dict = {
        0: {
            "block_type": "tool_use",
            "tool_id": "toolu_789",
            "tool_name": "broken",
            "args_json": '{"incomplete": ',
        }
    }
    native = make_stream_event({"type": "content_block_stop", "index": 0})
    unified = ClaudeSDKAdapter.to_unified_event(native, context)

    assert unified.type == StreamEventType.TOOL_CALL_STOP
    assert unified.tool_input == {}


def test_tool_use_block_stop_without_tool_id():
    """Test tool_use stop without tool_id generates a UUID."""
    context: dict = {
        0: {
            "block_type": "tool_use",
            "tool_id": None,
            "tool_name": "search",
            "args_json": "{}",
        }
    }
    native = make_stream_event({"type": "content_block_stop", "index": 0})
    unified = ClaudeSDKAdapter.to_unified_event(native, context)

    assert unified.type == StreamEventType.TOOL_CALL_STOP
    assert unified.tool_call_id is not None
    assert len(unified.tool_call_id) > 0


# ==============================================================================
# Context Management Tests
# ==============================================================================


def test_context_populated_on_block_start():
    """Test that context is populated on content_block_start."""
    context: dict = {}
    native = make_stream_event(
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {"type": "tool_use", "id": "toolu_abc", "name": "calc"},
        }
    )
    ClaudeSDKAdapter.to_unified_event(native, context)

    assert 2 in context
    assert context[2]["block_type"] == "tool_use"
    assert context[2]["tool_id"] == "toolu_abc"
    assert context[2]["tool_name"] == "calc"
    assert context[2]["args_json"] == ""


def test_context_cleared_on_block_stop():
    """Test that context entry is removed on content_block_stop."""
    context: dict = {0: {"block_type": "text"}}
    native = make_stream_event({"type": "content_block_stop", "index": 0})
    ClaudeSDKAdapter.to_unified_event(native, context)

    assert 0 not in context


def test_multiple_blocks_independent_context():
    """Test that multiple blocks maintain independent context."""
    context: dict = {}

    # Start two blocks
    ClaudeSDKAdapter.to_unified_event(
        make_stream_event(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text"},
            }
        ),
        context,
    )
    ClaudeSDKAdapter.to_unified_event(
        make_stream_event(
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "t1", "name": "search"},
            }
        ),
        context,
    )

    assert 0 in context
    assert 1 in context
    assert context[0]["block_type"] == "text"
    assert context[1]["block_type"] == "tool_use"


# ==============================================================================
# Unknown Event Type Handling
# ==============================================================================


def test_unknown_event_type_fallback():
    """Test unknown event type returns MESSAGE_START fallback."""
    native = make_stream_event({"type": "unknown_event_type"})
    unified = ClaudeSDKAdapter.to_unified_event(native)

    assert unified.type == StreamEventType.MESSAGE_START


def test_unknown_content_block_type_fallback():
    """Test unknown content block type returns MESSAGE_START."""
    native = make_stream_event(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "unknown_block_type"},
        }
    )
    context: dict = {}
    unified = ClaudeSDKAdapter.to_unified_event(native, context)

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
    unified = ClaudeSDKAdapter.to_unified_event(native)

    assert unified.type == StreamEventType.TEXT_DELTA
    assert unified.part_id == 0


def test_non_dict_event_fallback():
    """Test non-dict event returns MESSAGE_START fallback."""

    class UnknownEvent:
        pass

    native = UnknownEvent()
    unified = ClaudeSDKAdapter.to_unified_event(native)

    assert unified.type == StreamEventType.MESSAGE_START


def test_none_context_fallback():
    """Test that None context works (stateless fallback)."""
    native = make_stream_event(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hello"},
        }
    )
    # Explicitly pass None for context
    unified = ClaudeSDKAdapter.to_unified_event(native, None)

    assert unified.type == StreamEventType.TEXT_DELTA
    assert unified.text == "hello"


# ==============================================================================
# Part Index Preservation Tests
# ==============================================================================


@pytest.mark.parametrize("index", [0, 1, 5, 10, 100])
def test_part_index_preserved_for_text_start(index: int):
    """Test that part_id is preserved across different indices for text start."""
    native = make_stream_event(
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text"},
        }
    )
    context: dict = {}
    unified = ClaudeSDKAdapter.to_unified_event(native, context)
    assert unified.part_id == index


@pytest.mark.parametrize("index", [0, 1, 5, 10, 100])
def test_part_index_preserved_for_delta(index: int):
    """Test that part_id is preserved for delta events."""
    native = make_stream_event(
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": "test"},
        }
    )
    unified = ClaudeSDKAdapter.to_unified_event(native)
    assert unified.part_id == index


@pytest.mark.parametrize("index", [0, 1, 5, 10, 100])
def test_part_index_preserved_for_stop(index: int):
    """Test that part_id is preserved for stop events."""
    context: dict = {index: {"block_type": "text"}}
    native = make_stream_event({"type": "content_block_stop", "index": index})
    unified = ClaudeSDKAdapter.to_unified_event(native, context)
    assert unified.part_id == index


# ==============================================================================
# Full Stream Sequence Tests
# ==============================================================================


def test_full_text_stream_sequence():
    """Test a complete text streaming sequence."""
    context: dict = {}
    events = [
        make_stream_event({"type": "message_start", "message": {"id": "msg_1"}}),
        make_stream_event(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text"},
            }
        ),
        make_stream_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello"},
            }
        ),
        make_stream_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": ", world!"},
            }
        ),
        make_stream_event({"type": "content_block_stop", "index": 0}),
        make_stream_event({"type": "message_stop"}),
    ]

    results = [ClaudeSDKAdapter.to_unified_event(e, context) for e in events]

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
    context: dict = {}
    events = [
        make_stream_event({"type": "message_start", "message": {"id": "msg_2"}}),
        make_stream_event(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_xyz",
                    "name": "search",
                },
            }
        ),
        make_stream_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"query"'},
            }
        ),
        make_stream_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": ': "test"}'},
            }
        ),
        make_stream_event({"type": "content_block_stop", "index": 0}),
        make_stream_event({"type": "message_stop"}),
    ]

    results = [ClaudeSDKAdapter.to_unified_event(e, context) for e in events]

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
    context: dict = {}
    events = [
        make_stream_event({"type": "message_start", "message": {"id": "msg_3"}}),
        # Thinking block
        make_stream_event(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking"},
            }
        ),
        make_stream_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Let me think..."},
            }
        ),
        make_stream_event({"type": "content_block_stop", "index": 0}),
        # Text block
        make_stream_event(
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text"},
            }
        ),
        make_stream_event(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "The answer is 42."},
            }
        ),
        make_stream_event({"type": "content_block_stop", "index": 1}),
        make_stream_event({"type": "message_stop"}),
    ]

    results = [ClaudeSDKAdapter.to_unified_event(e, context) for e in events]

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
    unified = ClaudeSDKAdapter.to_unified_event(native)

    assert unified.type == StreamEventType.TEXT_DELTA
    assert unified.text == "Tool output text"
