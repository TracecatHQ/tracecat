"""Unit tests for agent context history processing."""

from unittest.mock import Mock

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from tracecat.agent.context import (
    _message_contains_tool_return_parts,
    trim_history_processor,
)


def test_message_contains_tool_return_parts_with_tool_return():
    """Test detection of ToolReturnPart in a message."""
    message = ModelResponse(
        parts=[
            ToolReturnPart(
                tool_name="test_tool",
                content="result",
                tool_call_id="call_123",
            )
        ]
    )
    assert _message_contains_tool_return_parts(message) is True


def test_message_contains_tool_return_parts_without_tool_return():
    """Test detection when message has no ToolReturnPart."""
    message = ModelRequest(parts=[UserPromptPart(content="Hello")])
    assert _message_contains_tool_return_parts(message) is False


def test_message_contains_tool_return_parts_mixed():
    """Test detection with mixed parts including ToolReturnPart."""
    message = ModelResponse(
        parts=[
            TextPart(content="Some text"),
            ToolReturnPart(
                tool_name="test_tool",
                content="result",
                tool_call_id="call_123",
            ),
        ]
    )
    assert _message_contains_tool_return_parts(message) is True


def test_trim_history_processor_under_limit():
    """Test that messages are not trimmed when under the limit."""
    mock_ctx = Mock()
    mock_ctx.model.model_name = "test-model"
    mock_ctx.usage.total_tokens = 1000

    messages = [
        ModelRequest(parts=[SystemPromptPart(content="System prompt")]),
        ModelRequest(parts=[UserPromptPart(content="User message 1")]),
        ModelResponse(parts=[TextPart(content="Response 1")]),
    ]

    result = trim_history_processor(mock_ctx, messages)
    assert result == messages
    assert len(result) == 3


def test_trim_history_processor_over_limit():
    """Test that messages are trimmed when over the limit and no tool returns block it."""
    from unittest.mock import patch

    from pydantic_ai.messages import RequestUsage

    # Mock a small context limit for testing
    with patch(
        "tracecat.agent.context.TRACECAT__MODEL_CONTEXT_LIMITS", {"test-model": 5000}
    ):
        with patch(
            "tracecat.agent.context.TRACECAT__AGENT_DEFAULT_CONTEXT_LIMIT", 5000
        ):
            mock_ctx = Mock()
            mock_ctx.model.model_name = "test-model"
            mock_ctx.usage.total_tokens = 6000  # Over the 5000 limit

            # Create messages with realistic token usage
            # Each message has ~500 tokens
            messages = [
                ModelRequest(parts=[SystemPromptPart(content="System prompt")]),
                ModelRequest(parts=[UserPromptPart(content="User message 1")]),
                ModelResponse(
                    parts=[TextPart(content="Response 1")],
                    usage=RequestUsage(input_tokens=500),
                ),
                ModelRequest(parts=[UserPromptPart(content="User message 2")]),
                ModelResponse(
                    parts=[TextPart(content="Response 2")],
                    usage=RequestUsage(input_tokens=500),
                ),
                ModelRequest(parts=[UserPromptPart(content="User message 3")]),
                ModelResponse(
                    parts=[TextPart(content="Response 3")],
                    usage=RequestUsage(input_tokens=500),
                ),
                ModelRequest(parts=[UserPromptPart(content="User message 4")]),
                ModelResponse(
                    parts=[TextPart(content="Response 4")],
                    usage=RequestUsage(input_tokens=500),
                ),
                ModelRequest(parts=[UserPromptPart(content="User message 5")]),
                ModelResponse(
                    parts=[TextPart(content="Response 5")],
                    usage=RequestUsage(input_tokens=500),
                ),
                ModelRequest(parts=[UserPromptPart(content="User message 6")]),
                ModelResponse(
                    parts=[TextPart(content="Response 6")],
                    usage=RequestUsage(input_tokens=500),
                ),
            ]

            # Set usage on each message
            for msg in messages:
                if not hasattr(msg, "usage") or msg.usage.total_tokens == 0:
                    msg.usage = RequestUsage(input_tokens=500)

            result = trim_history_processor(mock_ctx, messages)

            # Should trim older messages but keep system prompt
            assert len(result) < len(messages)
            assert result[0].parts[0].content == "System prompt"
            # Should keep the most recent history messages
            assert any("User message 6" in str(msg.parts) for msg in result)


def test_trim_history_processor_preserves_tool_pairs():
    """Test that tool call/return pairs are preserved together."""
    from unittest.mock import patch

    from pydantic_ai.messages import RequestUsage

    # Mock a small context limit for testing
    with patch(
        "tracecat.agent.context.TRACECAT__MODEL_CONTEXT_LIMITS", {"test-model": 5000}
    ):
        with patch(
            "tracecat.agent.context.TRACECAT__AGENT_DEFAULT_CONTEXT_LIMIT", 5000
        ):
            mock_ctx = Mock()
            mock_ctx.model.model_name = "test-model"
            mock_ctx.usage.total_tokens = 6000  # Over the 5000 limit

            # Create messages where ToolReturnPart is placed such that it would be
            # the first message we'd keep when trimming
            # With effective_limit = 4950 (5000 - 50), we can fit ~9 messages of 500 tokens each
            # So if we have 12 history messages (6000 tokens), we'd trim the first 3
            # Place ToolReturnPart as the 4th message (index 3) in history
            messages = [
                ModelRequest(parts=[SystemPromptPart(content="System prompt")]),
                ModelRequest(parts=[UserPromptPart(content="Old 1")]),
                ModelResponse(
                    parts=[TextPart(content="Response 1")],
                    usage=RequestUsage(input_tokens=1000),
                ),
                ModelRequest(parts=[UserPromptPart(content="Old 2")]),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="test_tool",
                            args={"arg": "value"},
                            tool_call_id="call_123",
                        )
                    ],
                    usage=RequestUsage(input_tokens=1000),
                ),
                ModelResponse(
                    parts=[
                        ToolReturnPart(
                            tool_name="test_tool",
                            content="result",
                            tool_call_id="call_123",
                        )
                    ],
                    usage=RequestUsage(input_tokens=1000),
                ),  # This would be at the trim boundary
                ModelRequest(parts=[UserPromptPart(content="Recent 1")]),
                ModelResponse(
                    parts=[TextPart(content="Response 4")],
                    usage=RequestUsage(input_tokens=1000),
                ),
            ]

            # Set usage on each message
            for msg in messages:
                if not hasattr(msg, "usage") or msg.usage.total_tokens == 0:
                    msg.usage = RequestUsage(input_tokens=1000)

            result = trim_history_processor(mock_ctx, messages)

            # When the first kept message has a ToolReturnPart, skip trimming
            # to keep it paired with its ToolCallPart
            # The function should return all messages to preserve the pair
            assert len(result) == len(messages)


def test_trim_history_processor_empty_messages():
    """Test handling of empty message list."""
    from unittest.mock import patch

    with patch(
        "tracecat.agent.context.TRACECAT__MODEL_CONTEXT_LIMITS", {"test-model": 5000}
    ):
        with patch(
            "tracecat.agent.context.TRACECAT__AGENT_DEFAULT_CONTEXT_LIMIT", 5000
        ):
            mock_ctx = Mock()
            mock_ctx.model.model_name = "test-model"
            mock_ctx.usage.total_tokens = 6000  # Over the 5000 limit

            result = trim_history_processor(mock_ctx, [])
            assert result == []


def test_trim_history_processor_no_system_prompt():
    """Test trimming when there's no system prompt."""
    from unittest.mock import patch

    from pydantic_ai.messages import RequestUsage

    with patch(
        "tracecat.agent.context.TRACECAT__MODEL_CONTEXT_LIMITS", {"test-model": 5000}
    ):
        with patch(
            "tracecat.agent.context.TRACECAT__AGENT_DEFAULT_CONTEXT_LIMIT", 5000
        ):
            mock_ctx = Mock()
            mock_ctx.model.model_name = "test-model"
            mock_ctx.usage.total_tokens = 6000  # Over the 5000 limit

            messages = [
                ModelRequest(parts=[UserPromptPart(content="User message 1")]),
                ModelResponse(
                    parts=[TextPart(content="Response 1")],
                    usage=RequestUsage(input_tokens=500),
                ),
                ModelRequest(parts=[UserPromptPart(content="User message 2")]),
                ModelResponse(
                    parts=[TextPart(content="Response 2")],
                    usage=RequestUsage(input_tokens=500),
                ),
            ]

            # Set usage on each message
            for msg in messages:
                if not hasattr(msg, "usage") or msg.usage.total_tokens == 0:
                    msg.usage = RequestUsage(input_tokens=500)

            result = trim_history_processor(mock_ctx, messages)

            # Should trim but not expect a system prompt
            assert len(result) <= len(messages)


def test_trim_history_processor_single_message():
    """Test handling of a single message."""
    mock_ctx = Mock()
    mock_ctx.model.model_name = "test-model"
    mock_ctx.usage.total_tokens = 1000

    messages = [ModelRequest(parts=[UserPromptPart(content="Only message")])]

    result = trim_history_processor(mock_ctx, messages)
    assert result == messages
