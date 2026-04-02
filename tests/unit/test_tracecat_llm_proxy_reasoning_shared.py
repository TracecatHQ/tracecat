from __future__ import annotations

import httpx
import pytest

from tracecat.agent.llm_proxy.provider_azure_ai import AzureAIAdapter
from tracecat.agent.llm_proxy.provider_bedrock import BedrockAdapter
from tracecat.agent.llm_proxy.requests import (
    anthropic_stream_events_from_response,
    normalize_anthropic_response,
)
from tracecat.agent.llm_proxy.types import (
    IngressFormat,
    NormalizedMessage,
    NormalizedMessagesRequest,
    NormalizedResponse,
)


def _base_request(
    *,
    provider: str,
    model: str,
) -> NormalizedMessagesRequest:
    return NormalizedMessagesRequest(
        provider=provider,
        model=model,
        messages=(NormalizedMessage(role="user", content="hello"),),
        output_format=IngressFormat.ANTHROPIC,
    )


def test_normalize_anthropic_response_preserves_thinking_blocks() -> None:
    normalized = normalize_anthropic_response(
        {
            "model": "claude-sonnet-4-5-20250929",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "Let me reason this through.",
                    "signature": "sig_123",
                },
                {"type": "text", "text": "Done."},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 5},
        }
    )

    assert normalized.content == [
        {
            "type": "thinking",
            "thinking": "Let me reason this through.",
            "signature": "sig_123",
        },
        "Done.",
    ]


def test_anthropic_stream_events_emit_thinking_and_signature_deltas() -> None:
    events = anthropic_stream_events_from_response(
        NormalizedResponse(
            provider="anthropic",
            model="claude-sonnet-4-5-20250929",
            content=[
                {
                    "type": "thinking",
                    "thinking": "Plan first.",
                    "signature": "sig_abc",
                },
                {"type": "text", "text": "Final answer."},
            ],
            finish_reason="end_turn",
            usage={"input_tokens": 3, "output_tokens": 5},
            raw={"id": "msg-1"},
        )
    )

    assert [event.event for event in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert events[1].payload["content_block"]["type"] == "thinking"
    assert events[2].payload["delta"] == {
        "type": "thinking_delta",
        "thinking": "Plan first.",
    }
    assert events[3].payload["delta"] == {
        "type": "signature_delta",
        "signature": "sig_abc",
    }


@pytest.mark.anyio
async def test_azure_ai_parse_response_preserves_reasoning_blocks() -> None:
    response = httpx.Response(
        200,
        json={
            "model": "claude-sonnet-4-5-20250929",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "Trace the request path.",
                    "signature": "sig_azure",
                },
                {"type": "text", "text": "Done."},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "lookup",
                    "input": {"query": "status"},
                },
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 4, "output_tokens": 7},
        },
    )

    parsed = await AzureAIAdapter().parse_response(
        response,
        _base_request(provider="azure_ai", model="claude-sonnet-4-5-20250929"),
    )

    assert parsed.content == [
        {
            "type": "thinking",
            "thinking": "Trace the request path.",
            "signature": "sig_azure",
        },
        "Done.",
    ]
    assert parsed.tool_calls[0].name == "lookup"
    assert parsed.finish_reason == "tool_use"


@pytest.mark.anyio
async def test_bedrock_parse_response_preserves_reasoning_blocks() -> None:
    response = httpx.Response(
        200,
        json={
            "modelId": "anthropic.claude-3-7-sonnet",
            "output": {
                "message": {
                    "content": [
                        {
                            "reasoningContent": {
                                "text": "Need to inspect the tool output.",
                                "signature": "sig_bedrock",
                            }
                        },
                        {"text": "Working on it."},
                        {
                            "toolUse": {
                                "toolUseId": "call_1",
                                "name": "lookup",
                                "input": {"query": "status"},
                            }
                        },
                    ]
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 2, "outputTokens": 4},
        },
    )

    parsed = await BedrockAdapter().parse_response(
        response,
        _base_request(provider="bedrock", model="anthropic.claude-3-7-sonnet"),
    )

    assert parsed.content == [
        {
            "type": "thinking",
            "thinking": "Need to inspect the tool output.",
            "signature": "sig_bedrock",
        },
        {"type": "text", "text": "Working on it."},
    ]
    assert parsed.tool_calls[0].name == "lookup"
    assert parsed.finish_reason == "tool_use"
