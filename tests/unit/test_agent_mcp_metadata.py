from __future__ import annotations

from unittest.mock import patch

import pytest
from claude_agent_sdk.types import (
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from pydantic import ValidationError

from tracecat.agent.mcp.metadata import (
    PROXY_TOOL_CALL_ID_KEY,
    PROXY_TOOL_METADATA_KEY,
    sanitize_message_tool_inputs,
    strip_proxy_tool_metadata,
)
from tracecat.agent.types import ClaudeSDKMessageTA


def test_strip_proxy_tool_metadata_removes_internal_key() -> None:
    assert strip_proxy_tool_metadata(
        {
            "url": "https://example.com",
            PROXY_TOOL_METADATA_KEY: {
                PROXY_TOOL_CALL_ID_KEY: "toolu_123",
            },
        }
    ) == {"url": "https://example.com"}


def test_sanitize_message_tool_inputs_strips_claude_tool_use_metadata() -> None:
    message = {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_123",
                "name": "mcp__tracecat-registry__core__http_request",
                "input": {
                    "url": "https://example.com",
                    PROXY_TOOL_METADATA_KEY: {
                        PROXY_TOOL_CALL_ID_KEY: "toolu_123",
                    },
                },
            }
        ],
    }

    sanitized = sanitize_message_tool_inputs(message)

    assert sanitized["content"][0]["input"] == {"url": "https://example.com"}
    assert message["content"][0]["input"][PROXY_TOOL_METADATA_KEY] == {
        PROXY_TOOL_CALL_ID_KEY: "toolu_123"
    }


def test_sanitize_message_tool_inputs_strips_pydantic_tool_call_metadata() -> None:
    message = {
        "kind": "response",
        "parts": [
            {
                "part_kind": "tool-call",
                "tool_name": "core.http_request",
                "tool_call_id": "call_123",
                "args": {
                    "url": "https://example.com",
                    PROXY_TOOL_METADATA_KEY: {
                        PROXY_TOOL_CALL_ID_KEY: "call_123",
                    },
                },
            }
        ],
    }

    sanitized = sanitize_message_tool_inputs(message)

    assert sanitized["parts"][0]["args"] == {"url": "https://example.com"}
    assert message["parts"][0]["args"][PROXY_TOOL_METADATA_KEY] == {
        PROXY_TOOL_CALL_ID_KEY: "call_123"
    }


def test_sanitize_message_tool_inputs_coerces_null_thinking_signature() -> None:
    message = {
        "role": "assistant",
        "model": "custom-model",
        "content": [
            {"type": "thinking", "thinking": "reasoning", "signature": None},
            {"type": "text", "text": "done"},
        ],
    }

    sanitized = sanitize_message_tool_inputs(message)

    assert sanitized["content"][0]["signature"] == ""
    assert message["content"][0]["signature"] is None
    validated = ClaudeSDKMessageTA.validate_python(sanitized)
    assert isinstance(validated, AssistantMessage)
    assert isinstance(validated.content[0], ThinkingBlock)


def test_sanitize_message_tool_inputs_drops_redacted_thinking_blocks() -> None:
    message = {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "model": "custom-model",
        "content": [
            {
                "type": "redacted_thinking",
                "data": "litellm_encrypted_reasoning:opaque",
            },
            {"type": "thinking", "thinking": "reasoning", "signature": "sig"},
            {"type": "text", "text": "done"},
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
    }

    with pytest.raises(ValidationError):
        ClaudeSDKMessageTA.validate_python(message)

    sanitized = sanitize_message_tool_inputs(message)

    assert [block["type"] for block in sanitized["content"]] == ["thinking", "text"]
    assert message["content"][0]["type"] == "redacted_thinking"
    validated = ClaudeSDKMessageTA.validate_python(sanitized)
    assert isinstance(validated, AssistantMessage)
    assert isinstance(validated.content[0], ThinkingBlock)
    assert isinstance(validated.content[1], TextBlock)


def test_sanitize_message_tool_inputs_keeps_message_with_only_redacted_thinking() -> (
    None
):
    message = {
        "role": "assistant",
        "model": "custom-model",
        "content": [{"type": "redacted_thinking", "data": "opaque"}],
    }

    validated = ClaudeSDKMessageTA.validate_python(
        sanitize_message_tool_inputs(message)
    )

    assert isinstance(validated, AssistantMessage)
    assert validated.content == []


def test_sanitize_message_tool_inputs_drops_and_logs_unknown_block_types() -> None:
    message = {
        "role": "assistant",
        "model": "custom-model",
        "content": [
            {"type": "future_block", "payload": "opaque"},
            {"type": "text", "text": "done"},
            {"type": "server_tool_use", "id": "srv_1", "name": "advisor", "input": {}},
            {
                "type": "advisor_tool_result",
                "tool_use_id": "srv_1",
                "content": {"type": "advisor_result"},
            },
        ],
    }

    with patch("tracecat.agent.mcp.metadata.logger") as mock_logger:
        sanitized = sanitize_message_tool_inputs(message)

    assert [block["type"] for block in sanitized["content"]] == [
        "text",
        "server_tool_use",
        "advisor_tool_result",
    ]
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.kwargs["block_types"] == ["future_block"]
    assert isinstance(ClaudeSDKMessageTA.validate_python(sanitized), AssistantMessage)


def test_sanitize_message_tool_inputs_keeps_supported_user_blocks() -> None:
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "hi"},
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"},
        ],
    }

    with patch("tracecat.agent.mcp.metadata.logger") as mock_logger:
        sanitized = sanitize_message_tool_inputs(message)

    assert sanitized == message
    mock_logger.warning.assert_not_called()


def test_sanitize_message_tool_inputs_keeps_legacy_untyped_blocks() -> None:
    legacy_messages = [
        AssistantMessage(
            content=[
                TextBlock(text="hello"),
                ToolUseBlock(id="toolu_1", name="lookup", input={"q": "x"}),
            ],
            model="claude",
        ),
        UserMessage(content=[ToolResultBlock(tool_use_id="toolu_1", content="ok")]),
    ]

    for legacy in legacy_messages:
        dumped = ClaudeSDKMessageTA.dump_python(legacy, mode="json")
        with patch("tracecat.agent.mcp.metadata.logger") as mock_logger:
            sanitized = sanitize_message_tool_inputs(dumped)

        assert ClaudeSDKMessageTA.validate_python(sanitized) == legacy
        mock_logger.warning.assert_not_called()
