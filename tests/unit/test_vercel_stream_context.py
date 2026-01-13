"""Unit tests for VercelStreamContext adapter.

Tests the state machine that converts UnifiedStreamEvent sequences
into Vercel AI SDK SSE frames.
"""

import json

import pytest

from tracecat.agent.adapter.vercel import (
    DataEventPayload,
    ReasoningDeltaEventPayload,
    ReasoningEndEventPayload,
    ReasoningStartEventPayload,
    TextDeltaEventPayload,
    TextEndEventPayload,
    TextStartEventPayload,
    ToolInputAvailableEventPayload,
    ToolInputDeltaEventPayload,
    ToolInputStartEventPayload,
    ToolOutputAvailableEventPayload,
    VercelSSEPayload,
    VercelStreamContext,
    format_sse,
)
from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent


async def collect_frames(
    ctx: VercelStreamContext, events: list[UnifiedStreamEvent]
) -> list[VercelSSEPayload]:
    """Helper to collect all SSE frames from a sequence of events."""
    frames: list[VercelSSEPayload] = []

    for event in events:
        async for frame in ctx.handle_event(event):
            frames.append(frame)

    # Collect any final end events
    for frame in ctx.collect_current_part_end_events():
        frames.append(frame)

    return frames


# ==============================================================================
# Basic Text Streaming Tests
# ==============================================================================


@pytest.mark.anyio
async def test_text_part_with_initial_content():
    """Test text part start with initial content."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(type=StreamEventType.TEXT_START, part_id=0, text="hello"),
    ]

    frames = await collect_frames(ctx, events)

    assert len(frames) == 3
    assert isinstance(frames[0], TextStartEventPayload)
    assert isinstance(frames[1], TextDeltaEventPayload)
    assert frames[1].delta == "hello"
    assert isinstance(frames[2], TextEndEventPayload)


@pytest.mark.anyio
async def test_text_part_with_deltas():
    """Test text part with delta events."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(type=StreamEventType.TEXT_START, part_id=0, text="hello"),
        UnifiedStreamEvent(type=StreamEventType.TEXT_DELTA, part_id=0, text=" world"),
        UnifiedStreamEvent(type=StreamEventType.TEXT_DELTA, part_id=0, text="!"),
    ]

    frames = await collect_frames(ctx, events)

    # Start, initial content, 2 deltas, end
    assert len(frames) == 5
    assert isinstance(frames[0], TextStartEventPayload)
    assert isinstance(frames[1], TextDeltaEventPayload)
    assert frames[1].delta == "hello"
    assert isinstance(frames[2], TextDeltaEventPayload)
    assert frames[2].delta == " world"
    assert isinstance(frames[3], TextDeltaEventPayload)
    assert frames[3].delta == "!"
    assert isinstance(frames[4], TextEndEventPayload)


@pytest.mark.anyio
async def test_text_part_with_empty_initial_content():
    """Test text part with empty initial content."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(type=StreamEventType.TEXT_START, part_id=0, text=""),
        UnifiedStreamEvent(type=StreamEventType.TEXT_DELTA, part_id=0, text="hello"),
    ]

    frames = await collect_frames(ctx, events)

    # Start (no initial delta for empty content), delta, end
    assert len(frames) == 3
    assert isinstance(frames[0], TextStartEventPayload)
    assert isinstance(frames[1], TextDeltaEventPayload)
    assert frames[1].delta == "hello"
    assert isinstance(frames[2], TextEndEventPayload)


# ==============================================================================
# Reasoning/Thinking Streaming Tests
# ==============================================================================


@pytest.mark.anyio
async def test_reasoning_part_with_initial_content():
    """Test reasoning part start with initial content."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(
            type=StreamEventType.THINKING_START, part_id=0, thinking="analyzing..."
        ),
    ]

    frames = await collect_frames(ctx, events)

    assert len(frames) == 3
    assert isinstance(frames[0], ReasoningStartEventPayload)
    assert isinstance(frames[1], ReasoningDeltaEventPayload)
    assert frames[1].delta == "analyzing..."
    assert isinstance(frames[2], ReasoningEndEventPayload)


@pytest.mark.anyio
async def test_reasoning_part_with_deltas():
    """Test reasoning part with delta events."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(
            type=StreamEventType.THINKING_START, part_id=0, thinking="step 1"
        ),
        UnifiedStreamEvent(
            type=StreamEventType.THINKING_DELTA, part_id=0, thinking=", step 2"
        ),
        UnifiedStreamEvent(
            type=StreamEventType.THINKING_DELTA, part_id=0, thinking=", done"
        ),
    ]

    frames = await collect_frames(ctx, events)

    assert len(frames) == 5
    assert isinstance(frames[0], ReasoningStartEventPayload)
    assert isinstance(frames[1], ReasoningDeltaEventPayload)
    assert isinstance(frames[2], ReasoningDeltaEventPayload)
    assert isinstance(frames[3], ReasoningDeltaEventPayload)
    assert frames[1].delta == "step 1"
    assert frames[2].delta == ", step 2"
    assert frames[3].delta == ", done"
    assert isinstance(frames[4], ReasoningEndEventPayload)


@pytest.mark.anyio
async def test_reasoning_part_with_empty_delta():
    """Test reasoning part with empty content delta."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(type=StreamEventType.THINKING_START, part_id=0, thinking=""),
        UnifiedStreamEvent(
            type=StreamEventType.THINKING_DELTA, part_id=0, thinking=None
        ),
        UnifiedStreamEvent(
            type=StreamEventType.THINKING_DELTA, part_id=0, thinking="content"
        ),
    ]

    frames = await collect_frames(ctx, events)

    # Start, no delta for None, delta for "content", end
    assert len(frames) == 3
    assert isinstance(frames[0], ReasoningStartEventPayload)
    # Frame 1 should be the actual content delta, not the None
    assert isinstance(frames[1], ReasoningDeltaEventPayload)
    assert frames[1].delta == "content"
    assert isinstance(frames[2], ReasoningEndEventPayload)


# ==============================================================================
# Tool Call Streaming Tests
# ==============================================================================


@pytest.mark.anyio
async def test_tool_call_start():
    """Test tool call start with tool name and call ID."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=0,
            tool_call_id="call_123",
            tool_name="search",
            tool_input={"query": "test"},
        ),
    ]

    frames = await collect_frames(ctx, events)

    assert len(frames) == 2
    assert isinstance(frames[0], ToolInputStartEventPayload)
    assert frames[0].toolCallId == "call_123"
    assert frames[0].toolName == "search"
    # Tool input available is emitted on finalization
    assert isinstance(frames[1], ToolInputAvailableEventPayload)
    assert frames[1].input == {"query": "test"}


@pytest.mark.anyio
async def test_tool_call_input_delta():
    """Test tool call with input delta events."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=0,
            tool_call_id="call_123",
            tool_name="search",
            tool_input={"query": "test"},
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_DELTA,
            part_id=0,
            text='{"limit": 10}',
        ),
    ]

    frames = await collect_frames(ctx, events)

    # start, delta, input available
    assert len(frames) == 3
    assert isinstance(frames[0], ToolInputStartEventPayload)
    assert isinstance(frames[1], ToolInputDeltaEventPayload)
    assert frames[1].inputTextDelta == '{"limit": 10}'
    assert isinstance(frames[2], ToolInputAvailableEventPayload)


@pytest.mark.anyio
async def test_tool_call_with_empty_args():
    """Test tool call with empty/null args."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=0,
            tool_call_id="call_456",
            tool_name="get_time",
            tool_input=None,
        ),
    ]

    frames = await collect_frames(ctx, events)

    # Start, input available
    assert len(frames) == 2
    assert isinstance(frames[0], ToolInputStartEventPayload)
    assert isinstance(frames[1], ToolInputAvailableEventPayload)
    assert frames[1].input == {}


# ==============================================================================
# Tool Execution Result Tests
# ==============================================================================


@pytest.mark.anyio
async def test_tool_execution_success():
    """Test successful tool execution with TOOL_RESULT."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=0,
            tool_call_id="call_123",
            tool_name="search",
            tool_input={"query": "test"},
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="call_123",
            tool_name="search",
            tool_output={"results": ["item1", "item2"]},
            is_error=False,
        ),
    ]

    frames = await collect_frames(ctx, events)

    # Tool start, input available, output available
    assert len(frames) == 3
    assert isinstance(frames[0], ToolInputStartEventPayload)
    assert isinstance(frames[1], ToolInputAvailableEventPayload)
    assert isinstance(frames[2], ToolOutputAvailableEventPayload)
    assert frames[2].toolCallId == "call_123"
    assert frames[2].output == {"results": ["item1", "item2"]}


@pytest.mark.anyio
async def test_data_event_emitted_before_tool_parts():
    """Ensure queued data parts stream before subsequent tool sections."""
    ctx = VercelStreamContext(message_id="msg_test")
    ctx.enqueue_data_event(
        DataEventPayload(
            type="data-approval-request",
            data=[{"toolCallId": "call_approve", "toolName": "search"}],
        )
    )

    events = [
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=0,
            tool_call_id="call_approve",
            tool_name="search",
            tool_input={"query": "test"},
        ),
    ]

    frames = await collect_frames(ctx, events)

    assert len(frames) >= 2
    assert isinstance(frames[0], DataEventPayload)
    assert frames[0].type == "data-approval-request"
    assert isinstance(frames[1], ToolInputStartEventPayload)


@pytest.mark.anyio
async def test_tool_execution_failure():
    """Test tool execution failure with is_error=True."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=0,
            tool_call_id="call_123",
            tool_name="search",
            tool_input={"query": "test"},
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="call_123",
            tool_name="search",
            tool_output="Validation error: query too short",
            is_error=True,
        ),
    ]

    frames = await collect_frames(ctx, events)

    # Tool start, input available, output with error
    assert len(frames) == 3
    assert isinstance(frames[0], ToolInputStartEventPayload)
    assert isinstance(frames[2], ToolOutputAvailableEventPayload)
    # Error should be in the output
    assert "errorText" in frames[2].output
    assert "Validation error" in frames[2].output["errorText"]


@pytest.mark.anyio
async def test_tool_result_without_tracked_tool_call_id():
    """Test TOOL_RESULT with untracked tool_call_id still emits tool output."""
    ctx = VercelStreamContext(message_id="msg_test")

    # Create a TOOL_RESULT with a tool_call_id that's not in the tool_index
    # This simulates a tool error that wasn't preceded by a tool call in this stream
    events = [
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="untracked_call_id",
            tool_name="search",
            tool_output="General error message",
            is_error=True,
        ),
    ]

    frames = await collect_frames(ctx, events)

    # Should emit tool output with errorText (not fallback to text).
    # Some implementations may synthesize a preceding input-available frame
    # even when the tool_call_id wasn't previously tracked. Accept either.
    assert len(frames) in (1, 2)
    if len(frames) == 2:
        # Optional synthesized input
        assert isinstance(frames[0], ToolInputAvailableEventPayload)
        assert frames[0].toolCallId == "untracked_call_id"
        output_frame = frames[1]
    else:
        output_frame = frames[0]

    assert isinstance(output_frame, ToolOutputAvailableEventPayload)
    assert output_frame.toolCallId == "untracked_call_id"
    assert "errorText" in output_frame.output
    assert "General error message" in output_frame.output["errorText"]


@pytest.mark.anyio
async def test_tool_retry_scenario_multiple_failures_then_success():
    """Test tool retry scenario: multiple failures followed by success.

    This tests a common agent workflow where a tool is called multiple times
    with validation errors before finally succeeding.
    """
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        # First attempt
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=0,
            tool_call_id="call_001",
            tool_name="calculate",
            tool_input={"x": "not_a_number"},
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="call_001",
            tool_name="calculate",
            tool_output="ValidationError: x must be a number",
            is_error=True,
        ),
        # Second attempt
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=1,
            tool_call_id="call_002",
            tool_name="calculate",
            tool_input={"x": 0},
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="call_002",
            tool_name="calculate",
            tool_output="ValueError: x cannot be zero",
            is_error=True,
        ),
        # Third attempt - success
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=2,
            tool_call_id="call_003",
            tool_name="calculate",
            tool_input={"x": 42},
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="call_003",
            tool_name="calculate",
            tool_output={"result": 84},
            is_error=False,
        ),
    ]

    frames = await collect_frames(ctx, events)

    # First attempt: start, input available, error output
    # Second attempt: start, input available, error output
    # Third attempt: start, input available, success output
    assert len(frames) == 9

    # Verify first attempt
    assert isinstance(frames[0], ToolInputStartEventPayload)
    assert frames[0].toolCallId == "call_001"
    assert isinstance(frames[2], ToolOutputAvailableEventPayload)
    assert "ValidationError" in frames[2].output["errorText"]

    # Verify second attempt
    assert isinstance(frames[3], ToolInputStartEventPayload)
    assert frames[3].toolCallId == "call_002"
    assert isinstance(frames[5], ToolOutputAvailableEventPayload)
    assert "ValueError" in frames[5].output["errorText"]

    # Verify third attempt - success
    assert isinstance(frames[6], ToolInputStartEventPayload)
    assert frames[6].toolCallId == "call_003"
    assert isinstance(frames[8], ToolOutputAvailableEventPayload)
    assert frames[8].output == {"result": 84}


# ==============================================================================
# Multi-Part Scenario Tests
# ==============================================================================


@pytest.mark.anyio
async def test_multiple_concurrent_text_parts():
    """Test multiple concurrent text parts at different indices."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(type=StreamEventType.TEXT_START, part_id=0, text="Part A"),
        UnifiedStreamEvent(type=StreamEventType.TEXT_START, part_id=1, text="Part B"),
        UnifiedStreamEvent(
            type=StreamEventType.TEXT_DELTA, part_id=0, text=" continues"
        ),
        UnifiedStreamEvent(type=StreamEventType.TEXT_DELTA, part_id=1, text=" also"),
    ]

    frames = await collect_frames(ctx, events)

    # Part 0: start, initial delta
    # Part 1: start, initial delta (Part 0 stays open)
    # Part 0: delta " continues"
    # Part 1: delta " also"
    # Both parts end on collect
    assert len(frames) == 8

    # Verify structure and part IDs are different
    assert isinstance(frames[0], TextStartEventPayload)  # Part 0 start
    assert isinstance(frames[1], TextDeltaEventPayload)  # Part 0 initial
    assert isinstance(frames[2], TextStartEventPayload)  # Part 1 start
    assert isinstance(frames[3], TextDeltaEventPayload)  # Part 1 initial
    assert isinstance(frames[4], TextDeltaEventPayload)  # Part 0 delta
    assert isinstance(frames[5], TextDeltaEventPayload)  # Part 1 delta
    assert isinstance(frames[6], TextEndEventPayload)  # Part 0 end
    assert isinstance(frames[7], TextEndEventPayload)  # Part 1 end

    part_0_id = frames[0].id
    part_1_id = frames[2].id
    assert part_0_id != part_1_id


@pytest.mark.anyio
async def test_switching_part_types():
    """Test switching between part types (text → tool → text)."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(
            type=StreamEventType.TEXT_START, part_id=0, text="Before tool"
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=1,
            tool_call_id="call_123",
            tool_name="search",
            tool_input={"q": "test"},
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="call_123",
            tool_name="search",
            tool_output={"results": []},
            is_error=False,
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TEXT_START, part_id=2, text="After tool"
        ),
    ]

    frames = await collect_frames(ctx, events)

    # Text part should end when tool starts
    # Tool part should end when result arrives
    # Second text part should start fresh
    assert len(frames) >= 7

    # Find the indices of different part types
    text_start_indices = [
        i for i, f in enumerate(frames) if isinstance(f, TextStartEventPayload)
    ]
    tool_start_indices = [
        i for i, f in enumerate(frames) if isinstance(f, ToolInputStartEventPayload)
    ]
    tool_output_indices = [
        i
        for i, f in enumerate(frames)
        if isinstance(f, ToolOutputAvailableEventPayload)
    ]

    assert len(text_start_indices) == 2  # Two text parts
    assert len(tool_start_indices) == 1  # One tool part
    assert len(tool_output_indices) == 1  # One tool result


@pytest.mark.anyio
async def test_multiple_tools_concurrent():
    """Test multiple tools executing concurrently."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=0,
            tool_call_id="call_001",
            tool_name="search",
            tool_input={"q": "test1"},
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=1,
            tool_call_id="call_002",
            tool_name="fetch",
            tool_input={"url": "https://example.com"},
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="call_001",
            tool_name="search",
            tool_output={"results": ["a", "b"]},
            is_error=False,
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id="call_002",
            tool_name="fetch",
            tool_output={"status": 200},
            is_error=False,
        ),
    ]

    frames = await collect_frames(ctx, events)

    # Both tools should have start and output events
    tool_starts = [f for f in frames if isinstance(f, ToolInputStartEventPayload)]
    tool_outputs = [f for f in frames if isinstance(f, ToolOutputAvailableEventPayload)]

    assert len(tool_starts) == 2
    assert len(tool_outputs) == 2

    # Verify tool call IDs are preserved
    assert {f.toolCallId for f in tool_starts} == {"call_001", "call_002"}
    assert {f.toolCallId for f in tool_outputs} == {"call_001", "call_002"}


# ==============================================================================
# Edge Cases & Error Handling Tests
# ==============================================================================


@pytest.mark.anyio
async def test_delta_for_unknown_part_index():
    """Test delta for unknown part index logs warning but doesn't crash."""
    ctx = VercelStreamContext(message_id="msg_test")

    # Send delta without starting a part
    events = [
        UnifiedStreamEvent(type=StreamEventType.TEXT_DELTA, part_id=99, text="orphan"),
    ]

    frames = await collect_frames(ctx, events)

    # Should handle gracefully with no frames
    assert len(frames) == 0


@pytest.mark.anyio
async def test_tool_call_delta_for_unknown_part():
    """Test TOOL_CALL_DELTA for unknown part index handles gracefully."""
    ctx = VercelStreamContext(message_id="msg_test")

    # Start a tool call
    events = [
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=0,
            tool_call_id="call_123",
            tool_name="search",
            tool_input={"query": "test"},
        ),
        # Send a delta with mismatched part_id (no matching part state)
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_DELTA,
            part_id=99,  # Different index
            text='{"extra": "data"}',
        ),
    ]

    frames = await collect_frames(ctx, events)

    # Should handle the error gracefully
    # Start, input available (no crash, delta ignored for unknown part)
    assert len(frames) >= 2
    assert isinstance(frames[0], ToolInputStartEventPayload)


@pytest.mark.anyio
async def test_part_finalization_with_no_active_parts():
    """Test part finalization with no active parts."""
    ctx = VercelStreamContext(message_id="msg_test")

    # Try to finalize without any active parts
    end_events = ctx.collect_current_part_end_events()

    assert len(end_events) == 0


@pytest.mark.anyio
async def test_collect_end_events_for_specific_index():
    """Test collecting end events for specific vs all parts."""
    ctx = VercelStreamContext(message_id="msg_test")

    events = [
        UnifiedStreamEvent(type=StreamEventType.TEXT_START, part_id=0, text="Part 0"),
        UnifiedStreamEvent(type=StreamEventType.TEXT_START, part_id=1, text="Part 1"),
    ]

    # Process events but don't collect end events yet
    for event in events:
        async for _ in ctx.handle_event(event):
            pass

    # Collect end events for specific index
    end_events_0 = ctx.collect_current_part_end_events(index=0)
    assert len(end_events_0) == 1
    assert isinstance(end_events_0[0], TextEndEventPayload)

    # Part 1 should still be active
    assert 1 in ctx.part_states

    # Collect all remaining
    end_events_all = ctx.collect_current_part_end_events()
    assert len(end_events_all) == 1
    assert isinstance(end_events_all[0], TextEndEventPayload)


# ==============================================================================
# State Management Tests
# ==============================================================================


@pytest.mark.anyio
async def test_part_state_tracking():
    """Test part state tracking across event lifecycle."""
    ctx = VercelStreamContext(message_id="msg_test")

    # Initially no parts
    assert len(ctx.part_states) == 0

    event = UnifiedStreamEvent(type=StreamEventType.TEXT_START, part_id=0, text="test")

    async for _ in ctx.handle_event(event):
        pass

    # Part should be registered
    assert 0 in ctx.part_states
    assert ctx.part_states[0].part_type == "text"
    assert ctx.part_states[0].open is True

    # Finalize
    ctx.collect_current_part_end_events()

    # Part should be removed
    assert 0 not in ctx.part_states


@pytest.mark.anyio
async def test_tool_index_management():
    """Test tool index management (tool_call_id → part index mapping)."""
    ctx = VercelStreamContext(message_id="msg_test")

    event = UnifiedStreamEvent(
        type=StreamEventType.TOOL_CALL_START,
        part_id=0,
        tool_call_id="call_123",
        tool_name="search",
        tool_input={},
    )

    async for _ in ctx.handle_event(event):
        pass

    # Tool index should be registered
    assert "call_123" in ctx.tool_index
    assert ctx.tool_index["call_123"] == 0

    # Process tool result
    result_event = UnifiedStreamEvent(
        type=StreamEventType.TOOL_RESULT,
        tool_call_id="call_123",
        tool_name="search",
        tool_output="result",
        is_error=False,
    )

    async for _ in ctx.handle_event(result_event):
        pass

    # Tool index should be cleaned up after finalization
    ctx.collect_current_part_end_events()
    assert "call_123" not in ctx.tool_index


@pytest.mark.anyio
async def test_tool_finished_state_tracking():
    """Test tool finished state tracking."""
    ctx = VercelStreamContext(message_id="msg_test")

    tool_call_id = "call_123"

    events = [
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            part_id=0,
            tool_call_id=tool_call_id,
            tool_name="search",
            tool_input={},
        ),
        UnifiedStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            tool_call_id=tool_call_id,
            tool_name="search",
            tool_output="result",
            is_error=False,
        ),
    ]

    await collect_frames(ctx, events)

    # Tool should be marked as finished
    assert tool_call_id in ctx.tool_finished
    assert ctx.tool_finished[tool_call_id] is True


@pytest.mark.anyio
async def test_tool_input_emitted_state_tracking():
    """Test tool input emitted state tracking."""
    ctx = VercelStreamContext(message_id="msg_test")

    tool_call_id = "call_123"

    event = UnifiedStreamEvent(
        type=StreamEventType.TOOL_CALL_START,
        part_id=0,
        tool_call_id=tool_call_id,
        tool_name="search",
        tool_input={"q": "test"},
    )

    async for _ in ctx.handle_event(event):
        pass

    # Input not yet emitted (will be emitted on finalization)
    assert tool_call_id in ctx.tool_input_emitted
    assert ctx.tool_input_emitted[tool_call_id] is False

    # Finalize - should emit input available
    ctx.collect_current_part_end_events()

    # Should now be marked as emitted
    assert ctx.tool_input_emitted[tool_call_id] is True


# ==============================================================================
# SSE Format Tests
# ==============================================================================


@pytest.mark.anyio
async def test_format_sse_produces_valid_output():
    """Test that format_sse produces valid SSE format."""
    payload = TextStartEventPayload(id="test_id")
    sse_string = format_sse(payload)

    assert sse_string.startswith("data: ")
    assert sse_string.endswith("\n\n")

    # Should be valid JSON
    json_str = sse_string[6:-2]  # Remove "data: " and "\n\n"
    data = json.loads(json_str)
    assert data["type"] == "text-start"
    assert data["id"] == "test_id"
