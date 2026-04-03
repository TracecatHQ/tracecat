"""Response normalization and Anthropic SSE rendering for the Tracecat LLM proxy.

This module handles:

- Normalizing raw OpenAI and Anthropic response payloads into NormalizedResponse.
- Rendering NormalizedResponse into Anthropic wire format (JSON + SSE events).
"""

from __future__ import annotations

from typing import Any

import orjson

from tracecat.agent.llm_proxy.provider_common import (
    anthropic_signature_delta_event,
    anthropic_thinking_block_start_event,
    anthropic_thinking_delta_event,
    is_anthropic_thinking_block,
    openai_finish_reason_to_anthropic,
)
from tracecat.agent.llm_proxy.types import (
    AnthropicStreamEvent,
    NormalizedResponse,
    NormalizedToolCall,
)


def _tool_arguments_to_openai(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, bytes):
        return arguments.decode("utf-8", errors="ignore")
    try:
        return orjson.dumps(arguments).decode("utf-8")
    except TypeError:
        return str(arguments)


def _normalized_tool_call_from_dict(data: dict[str, Any]) -> NormalizedToolCall:
    raw_function = data.get("function")
    function = raw_function if isinstance(raw_function, dict) else {}
    return NormalizedToolCall(
        id=str(data.get("id", "")),
        name=str(function.get("name", data.get("name", ""))),
        arguments=_parse_json_value(function.get("arguments", data.get("arguments"))),
    )


def _parse_json_value(value: Any) -> Any:
    try:
        return orjson.loads(value)
    except (orjson.JSONDecodeError, TypeError):
        return (
            value.decode("utf-8", errors="ignore")
            if isinstance(value, bytes)
            else value
        )


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


def normalize_openai_response(payload: dict[str, Any]) -> NormalizedResponse:
    """Normalize an OpenAI chat-completions payload."""
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    tool_calls = tuple(
        _normalized_tool_call_from_dict(item)
        for item in message.get("tool_calls", [])
        if isinstance(item, dict)
    )
    usage = payload.get("usage") or {}
    raw_finish_reason = choice.get("finish_reason")
    finish_reason = (
        openai_finish_reason_to_anthropic(raw_finish_reason)
        if isinstance(raw_finish_reason, str)
        else raw_finish_reason
    )
    return NormalizedResponse(
        provider="openai",
        model=str(payload.get("model", "")),
        content=message.get("content"),
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage={
            "input_tokens": int(
                usage.get("input_tokens", usage.get("prompt_tokens", 0))
            ),
            "output_tokens": int(
                usage.get("output_tokens", usage.get("completion_tokens", 0))
            ),
        },
        raw=payload,
    )


def normalize_anthropic_response(payload: dict[str, Any]) -> NormalizedResponse:
    """Normalize an Anthropic messages payload."""
    content = payload.get("content")
    text_parts: list[Any] = []
    tool_calls: list[NormalizedToolCall] = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                text_parts.append(item)
                continue
            item_type = item.get("type")
            if item_type == "tool_use":
                tool_calls.append(
                    NormalizedToolCall(
                        id=str(item.get("id", "")),
                        name=str(item.get("name", "")),
                        arguments=item.get("input") or {},
                    )
                )
            elif item_type == "text":
                text_parts.append(item.get("text", ""))
            elif is_anthropic_thinking_block(item):
                text_parts.append({str(key): value for key, value in item.items()})
            else:
                text_parts.append(item)
    elif content is not None:
        text_parts.append(content)
    raw_usage = payload.get("usage")
    usage = (
        {
            "input_tokens": int(raw_usage.get("input_tokens", 0)),
            "output_tokens": int(raw_usage.get("output_tokens", 0)),
        }
        if isinstance(raw_usage, dict)
        else {}
    )
    return NormalizedResponse(
        provider="anthropic",
        model=str(payload.get("model", "")),
        content=text_parts if len(text_parts) != 1 else text_parts[0],
        tool_calls=tuple(tool_calls),
        finish_reason=payload.get("stop_reason"),
        usage=usage,
        raw=payload,
    )


# ---------------------------------------------------------------------------
# Anthropic response rendering
# ---------------------------------------------------------------------------


def render_anthropic_response(response: NormalizedResponse) -> dict[str, Any]:
    """Render a normalized response into an Anthropic messages payload."""
    content: list[dict[str, Any]] = []
    if response.content is not None:
        if isinstance(response.content, list):
            content.extend(
                item if isinstance(item, dict) else {"type": "text", "text": str(item)}
                for item in response.content
            )
        else:
            content.append({"type": "text", "text": str(response.content)})
    for tool_call in response.tool_calls:
        content.append(
            {
                "type": "tool_use",
                "id": tool_call.id,
                "name": tool_call.name,
                "input": tool_call.arguments,
            }
        )
    return {
        "id": response.raw.get("id", f"tracecat-{response.provider}"),
        "type": "message",
        "role": "assistant",
        "model": response.model,
        "content": content,
        "stop_reason": response.finish_reason,
        "usage": {
            "input_tokens": response.usage.get("input_tokens", 0),
            "output_tokens": response.usage.get("output_tokens", 0),
        },
    }


def render_anthropic_stream_event(event: AnthropicStreamEvent) -> bytes:
    """Render a single Anthropic-compatible SSE event."""
    return _sse_event(event.event, event.payload)


def anthropic_stream_events_from_response(
    response: NormalizedResponse,
) -> list[AnthropicStreamEvent]:
    """Build synthetic Anthropic SSE events from a completed response."""
    rendered = render_anthropic_response(response)
    input_tokens = rendered["usage"]["input_tokens"]
    output_tokens = rendered["usage"]["output_tokens"]
    message_start = {
        "type": "message_start",
        "message": {
            "id": rendered["id"],
            "type": "message",
            "role": "assistant",
            "model": rendered["model"],
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": 0,
            },
        },
    }
    events = [AnthropicStreamEvent("message_start", message_start)]

    for index, block in enumerate(rendered["content"]):
        if not isinstance(block, dict):
            block = {"type": "text", "text": str(block)}
        block_type = block.get("type")
        if block_type == "thinking":
            events.append(anthropic_thinking_block_start_event(index))
            if isinstance(thinking := block.get("thinking"), str) and thinking:
                events.append(anthropic_thinking_delta_event(index, thinking))
            if isinstance(signature := block.get("signature"), str) and signature:
                events.append(anthropic_signature_delta_event(index, signature))
            events.append(
                AnthropicStreamEvent(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": index},
                )
            )
            continue
        if block_type == "tool_use":
            partial_json = _tool_arguments_to_openai(block.get("input", {}))
            events.extend(
                [
                    AnthropicStreamEvent(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": index,
                            "content_block": {
                                "type": "tool_use",
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "input": {},
                            },
                        },
                    ),
                    AnthropicStreamEvent(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": index,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": partial_json,
                            },
                        },
                    ),
                    AnthropicStreamEvent(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": index},
                    ),
                ]
            )
            continue

        text = block.get("text", "")
        events.extend(
            [
                AnthropicStreamEvent(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                AnthropicStreamEvent(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "text_delta", "text": str(text)},
                    },
                ),
                AnthropicStreamEvent(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": index},
                ),
            ]
        )

    events.extend(
        [
            AnthropicStreamEvent(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {
                        "stop_reason": rendered["stop_reason"],
                        "stop_sequence": None,
                    },
                    "usage": {"output_tokens": output_tokens},
                },
            ),
            AnthropicStreamEvent("message_stop", {"type": "message_stop"}),
        ]
    )
    return events


def stream_anthropic_response(response: NormalizedResponse) -> list[bytes]:
    """Render a normalized response into Anthropic-compatible SSE events."""
    return [
        render_anthropic_stream_event(event)
        for event in anthropic_stream_events_from_response(response)
    ]


def _sse_event(event_name: str, payload: dict[str, Any]) -> bytes:
    return (
        f"event: {event_name}\n".encode() + b"data: " + orjson.dumps(payload) + b"\n\n"
    )
