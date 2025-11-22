from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from tracecat.agent.context import prune_history
from tracecat.config import TRACECAT__MODEL_CONTEXT_LIMITS


def test_prune_history_returns_messages_without_modification() -> None:
    messages = [ModelRequest(parts=[UserPromptPart("hello")])]
    result = prune_history(messages, "unknown-model")
    assert result == messages


def test_prune_history_keeps_recent_messages_when_under_limit() -> None:
    msg1 = ModelRequest(parts=[UserPromptPart("first message")])
    msg2 = ModelResponse(parts=[TextPart("response")])
    messages = [msg1, msg2]
    result = prune_history(messages, "gpt-4o-mini")
    assert result == messages


def test_prune_history_drops_messages_when_over_limit(monkeypatch) -> None:
    monkeypatch.setitem(TRACECAT__MODEL_CONTEXT_LIMITS, "test-model", 50)

    msg1 = ModelRequest(parts=[UserPromptPart("message 1 " * 2)])
    msg2 = ModelResponse(parts=[TextPart("message 2 " * 2)])
    msg3 = ModelRequest(parts=[UserPromptPart("message 3 " * 2)])
    messages = [msg1, msg2, msg3]

    result = prune_history(messages, "test-model")

    assert len(result) < 3
    assert result[-1] == msg3
    assert msg1 not in result


def test_prune_history_truncates_large_tool_outputs(monkeypatch) -> None:
    monkeypatch.setattr("tracecat.agent.context.TRACECAT__AGENT_TOOL_OUTPUT_LIMIT", 20)

    long_content = "a" * 100
    user_msg = ModelRequest(parts=[UserPromptPart("test")])
    tool_call_msg = ModelResponse(
        parts=[ToolCallPart(tool_name="test", args={"query": "test"}, tool_call_id="1")]
    )
    tool_return_msg = ModelRequest(
        parts=[ToolReturnPart(tool_name="test", content=long_content, tool_call_id="1")]
    )

    result = prune_history([user_msg, tool_call_msg, tool_return_msg], "gpt-4o-mini")

    assert len(result) == 3
    truncated_part = result[2].parts[0]
    assert isinstance(truncated_part, ToolReturnPart)
    assert len(truncated_part.content) < 100
    assert "truncated" in truncated_part.content
    assert truncated_part.content.startswith("a" * 20)


def test_prune_history_preserves_system_prompt(monkeypatch) -> None:
    monkeypatch.setitem(TRACECAT__MODEL_CONTEXT_LIMITS, "test-model", 30)

    sys_msg = ModelRequest(parts=[SystemPromptPart("system prompt")])
    msg_filler = ModelRequest(parts=[UserPromptPart("B" * 20)])
    messages = [sys_msg, msg_filler, msg_filler]

    result = prune_history(messages, "test-model")

    assert result[0] == sys_msg
    assert len(result) == 1


def test_prune_history_orphaned_tool_return() -> None:
    """Test that orphaned tool returns at the start of history are removed."""
    tool_msg = ModelRequest(
        parts=[ToolReturnPart(tool_name="test", content="res", tool_call_id="1")]
    )
    user_msg = ModelRequest(parts=[UserPromptPart("next")])
    messages = [tool_msg, user_msg]

    result = prune_history(messages, "gpt-4o-mini")

    # The orphaned tool return should be removed
    assert len(result) == 1
    assert result[0] == user_msg


def test_prune_history_removes_orphaned_tool_returns_from_pruning(monkeypatch) -> None:
    """Test that tool returns are removed when their tool calls are pruned."""
    # Set a very small context limit to force pruning
    monkeypatch.setitem(TRACECAT__MODEL_CONTEXT_LIMITS, "test-model", 150)

    # Create a sequence: user -> tool_call -> tool_result -> user -> tool_call -> tool_result
    msg1 = ModelRequest(parts=[UserPromptPart("First question with some padding text")])
    msg2 = ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="search", args={"query": "test"}, tool_call_id="call_1"
            )
        ]
    )
    msg3 = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="search", content="result 1", tool_call_id="call_1"
            )
        ]
    )
    msg4 = ModelRequest(parts=[UserPromptPart("Second question")])
    msg5 = ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="search", args={"query": "test2"}, tool_call_id="call_2"
            )
        ]
    )
    msg6 = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="search", content="result 2", tool_call_id="call_2"
            )
        ]
    )

    messages = [msg1, msg2, msg3, msg4, msg5, msg6]

    result = prune_history(messages, "test-model")

    # The older messages (msg1, msg2, msg3) should be pruned
    # If msg2 (tool call) is pruned but msg3 (tool result) is kept, we'd get an API error
    # Our fix should remove msg3 as well since msg2 is missing

    # Check that we don't have orphaned tool returns
    result_tool_call_ids = set()
    for msg in result:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    result_tool_call_ids.add(part.tool_call_id)

    # Verify all tool returns have corresponding tool calls
    for msg in result:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    assert part.tool_call_id in result_tool_call_ids, (
                        f"Tool return {part.tool_call_id} has no corresponding tool call"
                    )


def test_prune_history_removes_multiple_orphaned_tool_returns(monkeypatch) -> None:
    """Test that multiple orphaned tool returns in the same message are removed."""
    monkeypatch.setitem(TRACECAT__MODEL_CONTEXT_LIMITS, "test-model", 100)

    # Create messages with multiple tool calls/returns that will be pruned
    msg1 = ModelResponse(
        parts=[
            ToolCallPart(tool_name="tool1", args={}, tool_call_id="call_1"),
            ToolCallPart(tool_name="tool2", args={}, tool_call_id="call_2"),
        ]
    )
    msg2 = ModelRequest(
        parts=[
            ToolReturnPart(tool_name="tool1", content="result1", tool_call_id="call_1"),
            ToolReturnPart(tool_name="tool2", content="result2", tool_call_id="call_2"),
        ]
    )
    msg3 = ModelRequest(parts=[UserPromptPart("Recent message that should be kept")])

    messages = [msg1, msg2, msg3]

    result = prune_history(messages, "test-model")

    # msg1 and msg2 should be pruned, leaving only msg3
    # If msg2 was kept but msg1 was dropped, we'd get an API error
    assert len(result) == 1
    assert result[0] == msg3
