"""Unit tests for agent context history processing."""

from unittest.mock import Mock, patch

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from tracecat.agent.context import (
    _clean_orphaned_tool_messages,
    _count_tokens,
    _get_tool_call_ids,
    _get_tool_return_ids,
    _has_tool_call,
    _has_tool_return,
    _truncate_content,
    trim_history_processor,
    truncate_tool_returns_processor,
)

# === Helper function tests ===


def test_count_tokens():
    """Test token counting heuristic."""
    msg = ModelRequest(parts=[UserPromptPart(content="Hello world")])
    tokens = _count_tokens(msg)
    # Should be roughly len(json) / 4
    assert tokens > 0
    assert isinstance(tokens, int)


def test_truncate_content_under_limit():
    """Test truncation when content is under limit."""
    content = "Short content"
    result = _truncate_content(content, 100)
    assert result == content


def test_truncate_content_over_limit():
    """Test truncation when content exceeds limit."""
    content = "A" * 1000
    result = _truncate_content(content, 100)
    assert len(result) < len(content)
    assert result.startswith("A" * 100)
    assert "truncated" in result
    assert "900 chars" in result


def test_has_tool_call_true():
    """Test detection of tool calls."""
    msg = ModelResponse(
        parts=[ToolCallPart(tool_name="test", args={}, tool_call_id="call_1")]
    )
    assert _has_tool_call(msg) is True


def test_has_tool_call_false_on_request():
    """Test that ModelRequest is not detected as having tool call."""
    msg = ModelRequest(parts=[UserPromptPart(content="Hello")])
    assert _has_tool_call(msg) is False


def test_has_tool_call_false_on_text_response():
    """Test that text response is not detected as having tool call."""
    msg = ModelResponse(parts=[TextPart(content="Hello")])
    assert _has_tool_call(msg) is False


def test_has_tool_return_true():
    """Test detection of tool returns."""
    msg = ModelRequest(
        parts=[
            ToolReturnPart(tool_name="test", content="result", tool_call_id="call_1")
        ]
    )
    assert _has_tool_return(msg) is True


def test_has_tool_return_false():
    """Test no false positive for tool returns."""
    msg = ModelRequest(parts=[UserPromptPart(content="Hello")])
    assert _has_tool_return(msg) is False


def test_get_tool_call_ids():
    """Test extraction of tool call IDs."""
    msg = ModelResponse(
        parts=[
            ToolCallPart(tool_name="test1", args={}, tool_call_id="call_1"),
            ToolCallPart(tool_name="test2", args={}, tool_call_id="call_2"),
            TextPart(content="Some text"),
        ]
    )
    ids = _get_tool_call_ids(msg)
    assert ids == {"call_1", "call_2"}


def test_get_tool_return_ids():
    """Test extraction of tool return IDs."""
    msg = ModelRequest(
        parts=[
            ToolReturnPart(tool_name="test1", content="r1", tool_call_id="call_1"),
            ToolReturnPart(tool_name="test2", content="r2", tool_call_id="call_2"),
        ]
    )
    ids = _get_tool_return_ids(msg)
    assert ids == {"call_1", "call_2"}


# === Orphan cleaning tests ===


def test_clean_orphaned_tool_messages_empty():
    """Test cleaning empty list."""
    result = _clean_orphaned_tool_messages([])
    assert result == []


def test_clean_orphaned_tool_messages_no_orphans():
    """Test that paired tool calls/returns are kept."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content="Hello")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="test", args={}, tool_call_id="call_1")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="test", content="result", tool_call_id="call_1"
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Done")]),
    ]
    result = _clean_orphaned_tool_messages(messages)
    assert len(result) == 4


def test_clean_orphaned_tool_messages_removes_orphan_call():
    """Test removal of tool call without matching return."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content="Hello")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="test", args={}, tool_call_id="call_1")]
        ),
        # Missing tool return!
        ModelResponse(parts=[TextPart(content="Done")]),
    ]
    result = _clean_orphaned_tool_messages(messages)
    # Should remove the orphaned tool call
    assert len(result) == 2
    assert not any(_has_tool_call(m) for m in result)


def test_clean_orphaned_tool_messages_removes_orphan_return():
    """Test removal of tool return without matching call."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content="Hello")]),
        # Missing tool call!
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="test", content="result", tool_call_id="call_1"
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Done")]),
    ]
    result = _clean_orphaned_tool_messages(messages)
    # Should remove the orphaned tool return
    assert len(result) == 2
    assert not any(_has_tool_return(m) for m in result)


# === Truncate tool returns processor tests ===


def test_truncate_tool_returns_processor_no_truncation_needed():
    """Test that small tool returns are unchanged."""
    mock_ctx = Mock()
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="test", content="short", tool_call_id="call_1")
            ]
        )
    ]

    with patch("tracecat.agent.context.TRACECAT__AGENT_TOOL_OUTPUT_LIMIT", 1000):
        result = truncate_tool_returns_processor(mock_ctx, messages)

    assert len(result) == 1
    assert result[0].parts[0].content == "short"


def test_truncate_tool_returns_processor_truncates_large_output():
    """Test that large tool returns are truncated."""
    mock_ctx = Mock()
    large_content = "A" * 10000
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="test", content=large_content, tool_call_id="call_1"
                )
            ]
        )
    ]

    with patch("tracecat.agent.context.TRACECAT__AGENT_TOOL_OUTPUT_LIMIT", 100):
        result = truncate_tool_returns_processor(mock_ctx, messages)

    assert len(result) == 1
    truncated_content = result[0].parts[0].content
    assert len(truncated_content) < len(large_content)
    assert "truncated" in truncated_content


def test_truncate_tool_returns_processor_preserves_model_response():
    """Test that ModelResponse messages pass through unchanged."""
    mock_ctx = Mock()
    messages = [ModelResponse(parts=[TextPart(content="A" * 10000)])]

    with patch("tracecat.agent.context.TRACECAT__AGENT_TOOL_OUTPUT_LIMIT", 100):
        result = truncate_tool_returns_processor(mock_ctx, messages)

    assert result == messages


def test_truncate_tool_returns_processor_truncates_large_dict():
    """Test that large dict tool returns are serialized and truncated."""
    mock_ctx = Mock()
    # Create a large dict that exceeds the limit when serialized
    large_dict = {"data": "A" * 10000, "nested": {"key": "value"}}
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="test", content=large_dict, tool_call_id="call_1"
                )
            ]
        )
    ]

    with patch("tracecat.agent.context.TRACECAT__AGENT_TOOL_OUTPUT_LIMIT", 100):
        result = truncate_tool_returns_processor(mock_ctx, messages)

    assert len(result) == 1
    truncated_content = result[0].parts[0].content
    # Content should now be a truncated string
    assert isinstance(truncated_content, str)
    assert "truncated" in truncated_content
    assert len(truncated_content) < 10000


def test_truncate_tool_returns_processor_truncates_large_list():
    """Test that large list tool returns are serialized and truncated."""
    mock_ctx = Mock()
    # Create a large list that exceeds the limit when serialized
    large_list = ["item" * 100 for _ in range(100)]
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="test", content=large_list, tool_call_id="call_1"
                )
            ]
        )
    ]

    with patch("tracecat.agent.context.TRACECAT__AGENT_TOOL_OUTPUT_LIMIT", 100):
        result = truncate_tool_returns_processor(mock_ctx, messages)

    assert len(result) == 1
    truncated_content = result[0].parts[0].content
    # Content should now be a truncated string
    assert isinstance(truncated_content, str)
    assert "truncated" in truncated_content


def test_truncate_tool_returns_processor_small_dict_unchanged():
    """Test that small dict tool returns are not modified."""
    mock_ctx = Mock()
    small_dict = {"key": "value"}
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="test", content=small_dict, tool_call_id="call_1"
                )
            ]
        )
    ]

    with patch("tracecat.agent.context.TRACECAT__AGENT_TOOL_OUTPUT_LIMIT", 1000):
        result = truncate_tool_returns_processor(mock_ctx, messages)

    assert len(result) == 1
    # Content should remain as the original dict
    assert result[0].parts[0].content == small_dict


# === Trim history processor tests ===


def test_trim_history_processor_under_limit():
    """Test that messages under token limit are unchanged."""
    mock_ctx = Mock()
    mock_ctx.model.model_name = "test-model"

    messages = [
        ModelRequest(parts=[UserPromptPart(content="Hello")]),
        ModelResponse(parts=[TextPart(content="Hi there")]),
    ]

    with patch(
        "tracecat.agent.context.TRACECAT__MODEL_CONTEXT_LIMITS", {"test-model": 100000}
    ):
        result = trim_history_processor(mock_ctx, messages)

    assert result == messages


def test_trim_history_processor_trims_over_limit():
    """Test that messages over token limit are trimmed."""
    mock_ctx = Mock()
    mock_ctx.model.model_name = "test-model"

    # Create many messages to exceed a small limit
    messages = [
        ModelRequest(parts=[UserPromptPart(content=f"Message {i}" * 100)])
        for i in range(20)
    ]

    with patch(
        "tracecat.agent.context.TRACECAT__MODEL_CONTEXT_LIMITS", {"test-model": 500}
    ):
        result = trim_history_processor(mock_ctx, messages)

    assert len(result) < len(messages)


def test_trim_history_processor_empty():
    """Test handling of empty message list."""
    mock_ctx = Mock()
    mock_ctx.model.model_name = "test-model"

    result = trim_history_processor(mock_ctx, [])
    assert result == []


def test_trim_history_processor_safe_cut_avoids_tool_return():
    """Test that trim doesn't cut at a tool return message."""
    mock_ctx = Mock()
    mock_ctx.model.model_name = "test-model"

    messages = [
        ModelRequest(parts=[UserPromptPart(content="A" * 1000)]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="test", args={}, tool_call_id="call_1")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="test", content="result", tool_call_id="call_1"
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Done")]),
        ModelRequest(parts=[UserPromptPart(content="Final")]),
    ]

    with patch(
        "tracecat.agent.context.TRACECAT__MODEL_CONTEXT_LIMITS", {"test-model": 500}
    ):
        result = trim_history_processor(mock_ctx, messages)

    # Should not start with a tool return
    if result:
        assert not _has_tool_return(result[0])


def test_trim_history_processor_uses_default_limit():
    """Test fallback to default context limit for unknown model."""
    mock_ctx = Mock()
    mock_ctx.model.model_name = "unknown-model"

    messages = [
        ModelRequest(parts=[UserPromptPart(content="Hello")]),
    ]

    with patch("tracecat.agent.context.TRACECAT__MODEL_CONTEXT_LIMITS", {}):
        with patch(
            "tracecat.agent.context.TRACECAT__AGENT_DEFAULT_CONTEXT_LIMIT", 100000
        ):
            result = trim_history_processor(mock_ctx, messages)

    assert result == messages


def test_trim_history_processor_cleans_orphans_before_trimming():
    """Test that orphaned tool messages are cleaned before trimming."""
    mock_ctx = Mock()
    mock_ctx.model.model_name = "test-model"

    messages = [
        ModelRequest(parts=[UserPromptPart(content="Hello")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="test", args={}, tool_call_id="orphan")]
        ),
        # Missing return - orphaned!
        ModelResponse(parts=[TextPart(content="Done")]),
    ]

    with patch(
        "tracecat.agent.context.TRACECAT__MODEL_CONTEXT_LIMITS", {"test-model": 100000}
    ):
        result = trim_history_processor(mock_ctx, messages)

    # Orphaned tool call should be removed
    assert not any(_has_tool_call(m) for m in result)
