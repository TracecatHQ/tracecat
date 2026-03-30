"""Anthropic bridge and upstream adapter helpers for the Tracecat LLM proxy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, NoReturn

import httpx
import orjson

from tracecat.agent.llm_proxy.anthropic_compat import (
    anthropic_tool_to_openai_tool as tool_definition_to_openai,
)
from tracecat.agent.llm_proxy.anthropic_compat import (
    anthropic_tools_to_openai_tools,
    create_tool_name_mapping,
    restore_tool_name,
    tool_choice_to_anthropic,
    tool_choice_to_openai,
    tool_definition_to_anthropic,
    tool_result_content_to_openai,
    tool_result_to_anthropic_block,
    truncate_tool_name,
)
from tracecat.agent.llm_proxy.requests import (
    filter_allowed_model_settings,
    messages_request_to_anthropic_payload,
    messages_request_to_openai_payload,
)
from tracecat.agent.llm_proxy.types import (
    AnthropicStreamEvent,
    IngressFormat,
    NormalizedMessagesRequest,
    NormalizedResponse,
    ProviderHTTPRequest,
)


def anthropic_request_to_openai_payload(
    request: NormalizedMessagesRequest,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Translate a normalized Anthropic request into OpenAI-compatible payload data."""
    openai_payload = messages_request_to_openai_payload(request)
    if request.tools:
        openai_tools, mapping = anthropic_tools_to_openai_tools(
            [dict(tool) for tool in request.tools]
        )
        openai_payload["tools"] = openai_tools
    else:
        mapping = {}
    if request.tool_choice is not None:
        tool_choice = tool_choice_to_openai(request.tool_choice)
        if isinstance(tool_choice, dict):
            if isinstance(function := tool_choice.get("function"), dict):
                function["name"] = truncate_tool_name(str(function.get("name", "")))
            elif "name" in tool_choice:
                tool_choice["name"] = truncate_tool_name(
                    str(tool_choice.get("name", ""))
                )
        openai_payload["tool_choice"] = tool_choice
    return openai_payload, mapping


_DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"


def _anthropic_messages_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1/messages") or base_url.endswith("/anthropic/v1/messages"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/messages"
    return f"{base_url}/v1/messages"


_FORWARDED_INGRESS_HEADERS = frozenset({"anthropic-version", "anthropic-beta"})


def _build_anthropic_headers(
    credentials: dict[str, str],
    *,
    ingress_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build Anthropic API headers.

    Headers from the ingress request (e.g. ``anthropic-version``,
    ``anthropic-beta``) are forwarded so the upstream API version matches
    what the calling SDK expects.  Credential-level overrides still take
    precedence.
    """
    api_key = credentials.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Anthropic provider requires ANTHROPIC_API_KEY")

    # Start with forwarded SDK headers, then layer credential overrides.
    # Header names are compared case-insensitively because the socket parser
    # preserves original casing (e.g. "Anthropic-Version" from the SDK).
    headers: dict[str, str] = {}
    if ingress_headers:
        lowered = {k.lower(): v for k, v in ingress_headers.items()}
        for key in _FORWARDED_INGRESS_HEADERS:
            if value := lowered.get(key):
                headers[key] = value

    headers.update(
        {
            "Content-Type": "application/json",
            "x-api-key": api_key,
        }
    )
    # Credential-level overrides win over ingress headers.
    if version := credentials.get("ANTHROPIC_VERSION"):
        headers["anthropic-version"] = version
    elif "anthropic-version" not in headers:
        headers["anthropic-version"] = "2023-06-01"

    if beta := credentials.get("ANTHROPIC_BETA"):
        headers["anthropic-beta"] = beta

    return headers


async def _raise_stream_http_error(
    response: httpx.Response,
    *,
    provider: str,
) -> NoReturn:
    body = await response.aread()
    detail = body.decode("utf-8", errors="ignore")[:512]
    raise RuntimeError(f"{provider} provider error: {response.status_code} {detail}")


@dataclass(slots=True)
class AnthropicAdapter:
    """Adapter for Anthropic upstreams.

    Implements PassthroughStreamAdapter: when the ingress format matches
    (Anthropic → Anthropic), the raw payload is forwarded with only auth
    headers and token-level model settings injected — no normalize/render
    round-trip.
    """

    provider: str = "anthropic"
    native_format: IngressFormat = IngressFormat.ANTHROPIC

    def prepare_request(
        self, request: NormalizedMessagesRequest, credentials: dict[str, str]
    ) -> ProviderHTTPRequest:
        payload = messages_request_to_anthropic_payload(request)
        base_url = request.base_url or _DEFAULT_ANTHROPIC_BASE_URL
        return ProviderHTTPRequest(
            method="POST",
            url=_anthropic_messages_url(base_url),
            headers=_build_anthropic_headers(credentials),
            json_body=payload,
            stream=request.stream,
        )

    async def parse_response(
        self, response: httpx.Response, request: NormalizedMessagesRequest
    ) -> NormalizedResponse:
        """Fallback for non-streaming execution path (used by _execute_request)."""
        del request
        if response.status_code >= 400:
            raise RuntimeError(
                f"{self.provider} provider error: "
                f"{response.status_code} {response.text[:512]}"
            )
        payload = response.json()
        from tracecat.agent.llm_proxy.requests import normalize_anthropic_response

        normalized = normalize_anthropic_response(payload)
        return NormalizedResponse(
            provider=self.provider,
            model=normalized.model,
            content=normalized.content,
            tool_calls=normalized.tool_calls,
            finish_reason=normalized.finish_reason,
            usage=normalized.usage,
            raw=payload,
        )

    async def passthrough_stream(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        credentials: dict[str, str],
        model_settings: dict[str, Any],
        *,
        model: str,
        base_url: str | None = None,
        ingress_headers: dict[str, str] | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream raw SSE bytes — Anthropic in, Anthropic out, no normalization."""
        base_url = base_url or _DEFAULT_ANTHROPIC_BASE_URL
        headers = _build_anthropic_headers(credentials, ingress_headers=ingress_headers)
        outbound_payload = dict(payload)

        # Inject token-level model settings into the raw payload
        if allowed := filter_allowed_model_settings(
            model_settings,
            provider=self.provider,
        ):
            outbound_payload.update(allowed)
        outbound_payload["stream"] = True
        outbound_payload["model"] = model

        async with client.stream(
            "POST",
            _anthropic_messages_url(base_url),
            headers=headers,
            content=orjson.dumps(outbound_payload),
        ) as response:
            if response.status_code >= 400:
                await _raise_stream_http_error(response, provider=self.provider)
            async for chunk in response.aiter_bytes():
                yield chunk

    async def stream_anthropic(
        self,
        client: httpx.AsyncClient,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
    ) -> AsyncIterator[AnthropicStreamEvent]:
        """Fallback: parse SSE into AnthropicStreamEvent (used when passthrough is skipped)."""
        from tracecat.agent.llm_proxy.provider_common import (
            iter_sse_events,
        )

        outbound = self.prepare_request(request, credentials)

        async with client.stream(
            outbound.method,
            outbound.url,
            headers=outbound.headers,
            content=outbound.body,
            json=outbound.json_body,
        ) as response:
            if response.status_code >= 400:
                await _raise_stream_http_error(response, provider=self.provider)

            async for sse_event in iter_sse_events(response):
                if not sse_event.data or sse_event.data == "[DONE]":
                    continue
                event_payload = orjson.loads(sse_event.data)
                if not isinstance(event_payload, dict):
                    continue
                event_name = sse_event.event or str(event_payload.get("type", ""))
                if event_name == "ping" or not event_name:
                    continue
                yield AnthropicStreamEvent(event_name, event_payload)


__all__ = [
    "AnthropicAdapter",
    "anthropic_request_to_openai_payload",
    "anthropic_tools_to_openai_tools",
    "create_tool_name_mapping",
    "restore_tool_name",
    "tool_choice_to_anthropic",
    "tool_choice_to_openai",
    "tool_definition_to_anthropic",
    "tool_definition_to_openai",
    "tool_result_content_to_openai",
    "tool_result_to_anthropic_block",
    "truncate_tool_name",
]
