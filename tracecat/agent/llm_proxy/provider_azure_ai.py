"""Azure AI Anthropic adapter and request-rewrite helpers for Tracecat."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, NoReturn

import httpx
import orjson

from tracecat.agent.llm_proxy.requests import messages_request_to_anthropic_payload
from tracecat.agent.llm_proxy.types import (
    NormalizedMessagesRequest,
    NormalizedResponse,
    ProviderHTTPRequest,
)


class AzureFoundryErrorStrings(StrEnum):
    """Known Azure Foundry error strings we use for bounded retries."""

    SET_EXTRA_PARAMETERS_TO_PASS_THROUGH = "Set extra-parameters to 'pass-through'"


_KNOWN_EXTRA_PARAMS = ("stream_options", "extra_body", "extra-parameters")


def _azure_anthropic_messages_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/v1/messages") or base.endswith("/anthropic/v1/messages"):
        return base
    if "/anthropic" in base:
        prefix = base.split("/anthropic", 1)[0].rstrip("/")
        return f"{prefix}/anthropic/v1/messages"
    return f"{base}/anthropic/v1/messages"


def _azure_anthropic_headers(
    *,
    api_key: str,
    version: str = "2023-06-01",
    beta: str | None = None,
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": version,
    }
    if beta:
        headers["anthropic-beta"] = beta
    return headers


def _provider_http_error(response: httpx.Response, provider: str) -> NoReturn:
    raise RuntimeError(
        f"{provider} provider error: {response.status_code} {response.text[:512]}"
    )


def _extract_usage(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage") or payload.get("usageMetadata") or {}
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": int(
            usage.get("input_tokens")
            or usage.get("inputTokens")
            or usage.get("promptTokenCount")
            or 0
        ),
        "output_tokens": int(
            usage.get("output_tokens")
            or usage.get("outputTokens")
            or usage.get("candidatesTokenCount")
            or 0
        ),
    }


def should_retry_llm_api_inside_llm_translation_on_http_error(
    error_text: str,
    *,
    should_drop_params: bool,
) -> bool:
    """Return True when the request can be rewritten and retried safely."""
    if should_drop_params and "Extra inputs are not permitted" in error_text:
        return True
    if "unknown field: parameter index is not a valid field" in error_text:
        return True
    if (
        AzureFoundryErrorStrings.SET_EXTRA_PARAMETERS_TO_PASS_THROUGH.value
        in error_text
    ):
        return True
    return False


def extract_params_to_drop_from_error_text(error_text: str) -> list[str]:
    """Extract parameter names from Azure's extra-parameters error text."""
    match = re.search(r"\[(.*?)\]", error_text)
    if not match:
        return []

    params: list[str] = []
    for raw_param in match.group(1).split(","):
        cleaned = raw_param.strip().strip("'").strip('"')
        if cleaned:
            params.append(cleaned)
    return params


def drop_extra_params_from_request_data(
    request_data: dict[str, Any],
    error_text: str,
) -> dict[str, Any]:
    """Remove the top-level parameters named in Azure's error text."""
    params_to_drop = extract_params_to_drop_from_error_text(error_text)
    if (
        not params_to_drop
        and AzureFoundryErrorStrings.SET_EXTRA_PARAMETERS_TO_PASS_THROUGH.value
        in error_text
    ):
        params_to_drop = list(_KNOWN_EXTRA_PARAMS)
    if not params_to_drop:
        return request_data

    rewritten = dict(request_data)
    for param in params_to_drop:
        rewritten.pop(param, None)
    return rewritten


def _remove_index_from_tool_calls(messages: list[Any]) -> None:
    for message in messages:
        if not isinstance(message, dict):
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if isinstance(tool_call, dict):
                tool_call.pop("index", None)


def transform_request_on_unprocessable_entity_error(
    request_data: dict[str, Any],
    error_text: str,
) -> dict[str, Any]:
    """Rewrite known-bad Azure request shapes before retrying."""
    rewritten = copy.deepcopy(request_data)

    if "unknown field: parameter index is not a valid field" in error_text:
        messages = rewritten.get("messages")
        if isinstance(messages, list):
            _remove_index_from_tool_calls(messages)
        return rewritten

    if (
        AzureFoundryErrorStrings.SET_EXTRA_PARAMETERS_TO_PASS_THROUGH.value
        in error_text
    ):
        return drop_extra_params_from_request_data(rewritten, error_text)

    if "Extra inputs are not permitted" in error_text:
        return drop_extra_params_from_request_data(rewritten, error_text)

    return rewritten


def _load_request_data(outbound_request: ProviderHTTPRequest) -> dict[str, Any]:
    if outbound_request.json_body is not None:
        return copy.deepcopy(outbound_request.json_body)
    if outbound_request.body:
        loaded = orjson.loads(outbound_request.body)
        if isinstance(loaded, dict):
            return loaded
    return {}


@dataclass(slots=True)
class AzureAIAdapter:
    """Adapter for Azure AI Anthropic endpoints."""

    provider: str = "azure_ai"

    def prepare_request(
        self, request: NormalizedMessagesRequest, credentials: dict[str, str]
    ) -> ProviderHTTPRequest:
        base_url = credentials.get("AZURE_API_BASE") or request.base_url
        api_key = credentials.get("AZURE_API_KEY")
        model_name = credentials.get("AZURE_AI_MODEL_NAME") or request.model
        if not base_url or not api_key or not model_name:
            raise ValueError(
                "Azure AI requires AZURE_API_BASE, AZURE_API_KEY, and AZURE_AI_MODEL_NAME"
            )

        payload = messages_request_to_anthropic_payload(request)
        payload["model"] = model_name
        return ProviderHTTPRequest(
            method="POST",
            url=_azure_anthropic_messages_url(base_url),
            headers=_azure_anthropic_headers(
                api_key=api_key,
                version=credentials.get("ANTHROPIC_VERSION", "2023-06-01"),
                beta=credentials.get("ANTHROPIC_BETA"),
            ),
            json_body=payload,
            stream=request.stream,
        )

    async def parse_response(
        self, response: httpx.Response, request: NormalizedMessagesRequest
    ) -> NormalizedResponse:
        del request
        if response.status_code >= 400:
            raise _provider_http_error(response, self.provider)
        payload = response.json()
        return NormalizedResponse(
            provider=self.provider,
            model=str(payload.get("model", "")),
            content=payload.get("content"),
            finish_reason=payload.get("stop_reason"),
            usage=_extract_usage(payload),
            raw=payload,
        )

    def prepare_retry_request(
        self,
        *,
        response: httpx.Response,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
        outbound_request: ProviderHTTPRequest,
        attempt: int,
    ) -> ProviderHTTPRequest | None:
        if attempt >= 2:
            return None

        error_text = response.text
        should_drop_params = bool(request.model_settings.get("drop_params", True))
        if not should_retry_llm_api_inside_llm_translation_on_http_error(
            error_text,
            should_drop_params=should_drop_params,
        ):
            return None

        request_data = _load_request_data(outbound_request)
        rewritten = transform_request_on_unprocessable_entity_error(
            request_data,
            error_text,
        )
        if rewritten == request_data:
            return None

        del credentials
        return replace(outbound_request, json_body=rewritten, body=None)
