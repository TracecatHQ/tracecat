from __future__ import annotations

from tracecat.agent.mcp.metadata import (
    PROXY_TOOL_CALL_ID_KEY,
    PROXY_TOOL_METADATA_KEY,
    sanitize_message_tool_inputs,
    strip_proxy_tool_metadata,
)


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
