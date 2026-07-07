from __future__ import annotations

from typing import cast

from claude_agent_sdk.types import (
    AssistantMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from tracecat.agent.session.search import (
    MAX_SEARCH_TEXT_CHARS,
    extract_search_text,
    extract_search_text_from_history_content,
)
from tracecat.agent.types import UnifiedMessage


def test_extract_search_text_from_pydantic_ai_text_tool_call_and_return() -> None:
    response_text = extract_search_text(
        ModelResponse(
            parts=[
                TextPart(content="I will look this up."),
                ToolCallPart(
                    tool_name="core.cases.search_cases",
                    args={"query": "alpha incident"},
                    tool_call_id="call_1",
                ),
            ]
        )
    )
    request_text = extract_search_text(
        ModelRequest(
            parts=[
                UserPromptPart(content="Find the alpha incident."),
                ToolReturnPart(
                    tool_name="core.cases.search_cases",
                    content={"rows": [{"title": "Alpha incident"}]},
                    tool_call_id="call_1",
                ),
            ]
        )
    )

    assert response_text is not None
    assert "I will look this up." in response_text
    assert "core.cases.search_cases" in response_text
    assert "alpha incident" in response_text
    assert request_text is not None
    assert "Find the alpha incident." in request_text
    assert "Alpha incident" in request_text


def test_extract_search_text_from_claude_sdk_message() -> None:
    text = extract_search_text(
        AssistantMessage(
            content=[
                TextBlock(text="We decided to rotate the key."),
                ToolUseBlock(
                    id="toolu_1",
                    name="core.table.lookup",
                    input={"table": "decisions", "key": "rotation"},
                ),
                ToolResultBlock(
                    tool_use_id="toolu_1",
                    content="Rotation decision recorded.",
                ),
            ],
            model="claude-sonnet-4",
        )
    )

    assert text is not None
    assert "We decided to rotate the key." in text
    assert "core.table.lookup" in text
    assert "Rotation decision recorded." in text


def test_extract_search_text_replaces_image_and_binary_content() -> None:
    text = extract_search_text(
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=[
                        "Please inspect this.",
                        ImageUrl(url="https://example.test/screenshot.png"),
                        BinaryContent(data=b"image-bytes", media_type="image/png"),
                    ]
                )
            ]
        )
    )

    assert text is not None
    assert "Please inspect this." in text
    assert text.count("[image]") == 2
    assert "image-bytes" not in text


def test_extract_search_text_tool_return_media_becomes_placeholder() -> None:
    # Media in tool returns must be redacted, never JSON-serialized (review P2).
    text = extract_search_text(
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="tools.browser.screenshot",
                    content=BinaryContent(
                        data=b"raw-image-bytes", media_type="image/png"
                    ),
                    tool_call_id="call_media",
                ),
                ToolReturnPart(
                    tool_name="tools.browser.snapshot",
                    content=[
                        "Page loaded.",
                        ImageUrl(url="https://example.test/s.png"),
                    ],
                    tool_call_id="call_mixed",
                ),
            ]
        )
    )

    assert text is not None
    assert "raw-image-bytes" not in text
    assert text.count("[image]") == 2
    assert "Page loaded." in text


def test_extract_search_text_caps_oversized_content() -> None:
    text = extract_search_text(ModelResponse(parts=[TextPart(content="alpha " * 3000)]))

    assert text is not None
    assert len(text) == MAX_SEARCH_TEXT_CHARS


def test_extract_search_text_returns_none_for_garbage_input() -> None:
    assert extract_search_text(cast(UnifiedMessage, object())) is None


def test_extract_history_content_indexes_claude_tool_blocks() -> None:
    # Persisted Claude SDK rows carry tool calls inline in message.content;
    # tool names and args must be searchable (Codex review P2).
    text = extract_search_text_from_history_content(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Looking that up."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "core.cases.get_case",
                        "input": {"case_id": "CASE-1234"},
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [{"type": "text", "text": "Case found."}],
                    },
                ],
            },
        }
    )

    assert text is not None
    assert "Looking that up." in text
    assert "core.cases.get_case" in text
    assert "CASE-1234" in text
    assert "Case found." in text
