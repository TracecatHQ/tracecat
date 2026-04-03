"""Azure OpenAI chat-completions adapter for the Tracecat-owned LLM proxy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, TypedDict

import httpx

from tracecat.agent.llm_proxy.provider_common import (
    AnthropicStreamingAdapter,
    ProviderRetryAdapter,
    base_url_from_request,
)
from tracecat.agent.llm_proxy.provider_openai import (
    _parse_chat_completions_response,
    _sanitize_chat_completions_payload_for_history_replay,
    _stream_openai_chat_completions,
    normalize_openai_payload,
)
from tracecat.agent.llm_proxy.requests import (
    messages_request_to_openai_payload,
)
from tracecat.agent.llm_proxy.tool_compat import (
    apply_tool_name_mapping,
    create_tool_name_mapping,
)
from tracecat.agent.llm_proxy.types import (
    AnthropicStreamEvent,
    NormalizedMessagesRequest,
    NormalizedResponse,
    ProviderHTTPRequest,
)


class _AzureOpenAIRequestParts(TypedDict):
    """Structured request fragments before Azure OpenAI request creation."""

    url: str
    deployment: str
    headers: dict[str, str]
    payload: dict[str, Any]


def _error_text(response: httpx.Response) -> str:
    try:
        return response.text
    except Exception:
        return ""


def _retry_with_payload(
    outbound_request: ProviderHTTPRequest,
    payload: dict[str, Any],
) -> ProviderHTTPRequest:
    return ProviderHTTPRequest(
        method=outbound_request.method,
        url=outbound_request.url,
        headers=outbound_request.headers,
        body=outbound_request.body,
        json_body=payload,
        stream=outbound_request.stream,
    )


def _mutate_openai_payload_for_retry(
    *,
    response: httpx.Response,
    outbound_request: ProviderHTTPRequest,
) -> ProviderHTTPRequest | None:
    payload = outbound_request.json_body
    if not isinstance(payload, dict):
        return None

    error_text = _error_text(response).lower()
    retry_payload = dict(payload)
    changed = False

    if "max_tokens" in error_text and "unsupported parameter" in error_text:
        if (
            "max_tokens" in retry_payload
            and "max_completion_tokens" not in retry_payload
        ):
            retry_payload["max_completion_tokens"] = retry_payload.pop("max_tokens")
            changed = True

    if "max_completion_tokens" in error_text and "unsupported parameter" in error_text:
        if (
            "max_completion_tokens" in retry_payload
            and "max_tokens" not in retry_payload
        ):
            retry_payload["max_tokens"] = retry_payload.pop("max_completion_tokens")
            changed = True

    for key in (
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "stop",
        "temperature",
    ):
        if key in retry_payload and key in error_text:
            retry_payload.pop(key, None)
            changed = True

    for key in ("parallel_tool_calls", "response_format", "verbosity", "tool_choice"):
        if key in retry_payload and key in error_text:
            retry_payload.pop(key, None)
            changed = True

    if changed:
        return _retry_with_payload(outbound_request, retry_payload)
    return None


def _azure_openai_request_parts(
    request: NormalizedMessagesRequest,
    credentials: dict[str, str],
    *,
    base_url: str,
) -> _AzureOpenAIRequestParts:
    """Build the mutable request fragments for Azure OpenAI chat completions."""

    payload = messages_request_to_openai_payload(request)
    _sanitize_chat_completions_payload_for_history_replay(payload)
    tool_name_mapping = create_tool_name_mapping(request.tools)
    apply_tool_name_mapping(payload, tool_name_mapping)
    normalize_openai_payload(
        payload,
        model=request.model,
        provider="azure_openai",
    )

    api_base = credentials.get("AZURE_API_BASE", base_url).rstrip("/")
    deployment = credentials.get("AZURE_DEPLOYMENT_NAME", request.model)
    api_version = credentials.get("AZURE_API_VERSION", request.api_version)
    if not api_version:
        raise ValueError("Azure OpenAI requires AZURE_API_VERSION")

    headers = {"Content-Type": "application/json"}
    payload["model"] = deployment
    if api_key := credentials.get("AZURE_API_KEY"):
        headers["api-key"] = api_key
    elif ad_token := credentials.get("AZURE_AD_TOKEN"):
        headers["Authorization"] = f"Bearer {ad_token}"
    else:
        raise ValueError("Azure OpenAI requires AZURE_API_KEY or AZURE_AD_TOKEN")

    return {
        "url": (
            f"{api_base}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={api_version}"
        ),
        "deployment": deployment,
        "headers": headers,
        "payload": payload,
    }


@dataclass(slots=True)
class AzureOpenAIAdapter(AnthropicStreamingAdapter, ProviderRetryAdapter):
    """Adapter for Azure OpenAI chat completions."""

    provider: str = "azure_openai"

    def prepare_request(
        self,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
    ) -> ProviderHTTPRequest:
        base_url = base_url_from_request(request, "https://api.openai.com")
        request_parts = _azure_openai_request_parts(
            request,
            credentials,
            base_url=base_url,
        )

        return ProviderHTTPRequest(
            method="POST",
            url=request_parts["url"],
            headers=request_parts["headers"],
            json_body=request_parts["payload"],
            stream=request.stream,
        )

    def prepare_retry_request(
        self,
        *,
        response: httpx.Response,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
        outbound_request: ProviderHTTPRequest,
    ) -> ProviderHTTPRequest | None:
        del request, credentials
        if response.status_code not in {400, 422}:
            return None
        return _mutate_openai_payload_for_retry(
            response=response,
            outbound_request=outbound_request,
        )

    async def parse_response(
        self,
        response: httpx.Response,
        request: NormalizedMessagesRequest,
    ) -> NormalizedResponse:
        return await _parse_chat_completions_response(
            response, request, provider=self.provider
        )

    async def stream_anthropic(
        self,
        client: httpx.AsyncClient,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
    ) -> AsyncIterator[AnthropicStreamEvent]:
        """Stream response using shared chat-completions logic."""
        outbound = self.prepare_request(request, credentials)
        async for event in _stream_openai_chat_completions(
            client, request, outbound, provider=self.provider
        ):
            yield event


__all__ = ["AzureOpenAIAdapter"]
