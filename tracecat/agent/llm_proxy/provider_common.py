"""Shared provider helpers for the Tracecat-owned LLM proxy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, NoReturn, Protocol, runtime_checkable

import httpx
import orjson

from tracecat.agent.llm_proxy.types import (
    AnthropicStreamEvent,
    IngressFormat,
    NormalizedMessagesRequest,
    NormalizedResponse,
    ProviderHTTPRequest,
)


class ProviderAdapter(Protocol):
    """Prepare and parse provider-specific requests."""

    provider: str

    def prepare_request(
        self,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
    ) -> ProviderHTTPRequest:
        raise NotImplementedError

    async def parse_response(
        self,
        response: httpx.Response,
        request: NormalizedMessagesRequest,
    ) -> NormalizedResponse:
        raise NotImplementedError


@runtime_checkable
class ProviderRetryAdapter(Protocol):
    """Provider adapter that can rewrite a failed request for retry."""

    def prepare_retry_request(
        self,
        *,
        response: httpx.Response,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
        outbound_request: ProviderHTTPRequest,
    ) -> ProviderHTTPRequest | None:
        raise NotImplementedError


@runtime_checkable
class PassthroughStreamAdapter(Protocol):
    """Provider that can stream raw bytes when ingress matches its native format."""

    native_format: IngressFormat

    def passthrough_stream(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        credentials: dict[str, str],
        model_settings: dict[str, Any],
        *,
        model: str,
        base_url: str | None = None,
    ) -> AsyncIterator[bytes]:
        raise NotImplementedError


@runtime_checkable
class AnthropicStreamingAdapter(Protocol):
    """Provider adapter that can emit Anthropic-compatible SSE events."""

    def stream_anthropic(
        self,
        client: httpx.AsyncClient,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
    ) -> AsyncIterator[AnthropicStreamEvent]:
        raise NotImplementedError


def base_url_from_request(request: NormalizedMessagesRequest, default: str) -> str:
    return (request.base_url or default).rstrip("/")


def json_bytes(payload: dict[str, object]) -> bytes:
    return orjson.dumps(payload)


def provider_http_error(response: httpx.Response, provider: str) -> NoReturn:
    raise RuntimeError(
        f"{provider} provider error: {response.status_code} {response.text[:512]}"
    )


async def raise_stream_http_error(
    response: httpx.Response,
    *,
    provider: str,
) -> NoReturn:
    body = await response.aread()
    detail = body.decode("utf-8", errors="ignore")[:512]
    raise RuntimeError(f"{provider} provider error: {response.status_code} {detail}")


@dataclass(frozen=True, slots=True)
class ServerSentEvent:
    event: str | None
    data: str


async def iter_sse_events(response: httpx.Response) -> AsyncIterator[ServerSentEvent]:
    event_name: str | None = None
    data_lines: list[str] = []

    async for raw_line in response.aiter_lines():
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                yield ServerSentEvent(event=event_name, data="\n".join(data_lines))
                event_name = None
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip() or None
            continue
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").removeprefix(" "))

    if data_lines:
        yield ServerSentEvent(event=event_name, data="\n".join(data_lines))


def openai_finish_reason_to_anthropic(value: str | None) -> str | None:
    match value:
        case "stop":
            return "end_turn"
        case "length":
            return "max_tokens"
        case "tool_calls" | "function_call":
            return "tool_use"
        case _:
            return value


def openai_stream_usage(payload: dict[str, object]) -> dict[str, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": int(usage.get("prompt_tokens", 0)),
        "output_tokens": int(usage.get("completion_tokens", 0)),
    }


def anthropic_message_start_event(
    *,
    message_id: str,
    model: str,
    input_tokens: int = 0,
) -> AnthropicStreamEvent:
    return AnthropicStreamEvent(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": 0,
                },
            },
        },
    )


def anthropic_text_block_start_event(index: int) -> AnthropicStreamEvent:
    return AnthropicStreamEvent(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        },
    )


def anthropic_text_delta_event(index: int, text: str) -> AnthropicStreamEvent:
    return AnthropicStreamEvent(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        },
    )


def anthropic_tool_block_start_event(
    *,
    index: int,
    tool_call_id: str,
    name: str,
) -> AnthropicStreamEvent:
    return AnthropicStreamEvent(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "tool_use",
                "id": tool_call_id,
                "name": name,
                "input": {},
            },
        },
    )


def anthropic_tool_delta_event(
    index: int,
    partial_json: str,
) -> AnthropicStreamEvent:
    return AnthropicStreamEvent(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {
                "type": "input_json_delta",
                "partial_json": partial_json,
            },
        },
    )


def anthropic_block_stop_event(index: int) -> AnthropicStreamEvent:
    return AnthropicStreamEvent(
        "content_block_stop",
        {"type": "content_block_stop", "index": index},
    )


def anthropic_message_delta_event(
    *,
    stop_reason: str | None,
    output_tokens: int = 0,
) -> AnthropicStreamEvent:
    return AnthropicStreamEvent(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": None,
            },
            "usage": {"output_tokens": output_tokens},
        },
    )


@dataclass(slots=True)
class OpenAIToolStreamState:
    content_index: int
    tool_call_id: str | None = None
    name: str | None = None
    started: bool = False
    stopped: bool = False
    pending_json: list[str] = field(default_factory=list)
