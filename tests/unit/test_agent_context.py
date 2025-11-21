from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from tracecat.agent.context import prune_history
from tracecat.config import (
    TRACECAT__MODEL_CONTEXT_LIMITS,
)


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
    tool_msg = ModelRequest(
        parts=[ToolReturnPart(tool_name="test", content=long_content, tool_call_id="1")]
    )

    result = prune_history([user_msg, tool_msg], "gpt-4o-mini")

    assert len(result) == 2
    truncated_part = result[1].parts[0]
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
    tool_msg = ModelRequest(
        parts=[ToolReturnPart(tool_name="test", content="res", tool_call_id="1")]
    )
    user_msg = ModelRequest(parts=[UserPromptPart("next")])
    messages = [tool_msg, user_msg]

    result = prune_history(messages, "gpt-4o-mini")

    assert len(result) == 1
    assert result[0] == user_msg
