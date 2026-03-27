"""OpenAI-family provider adapters for the Tracecat-owned LLM proxy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
import orjson

from tracecat.agent.llm_proxy.anthropic_compat import (
    create_tool_name_mapping,
    restore_tool_name,
)
from tracecat.agent.llm_proxy.provider_common import (
    AnthropicStreamingAdapter,
    OpenAIToolStreamState,
    ProviderRetryAdapter,
    anthropic_block_stop_event,
    anthropic_message_delta_event,
    anthropic_message_start_event,
    anthropic_text_block_start_event,
    anthropic_text_delta_event,
    anthropic_tool_block_start_event,
    anthropic_tool_delta_event,
    base_url_from_request,
    iter_sse_events,
    openai_finish_reason_to_anthropic,
    openai_stream_usage,
    provider_http_error,
    raise_stream_http_error,
)
from tracecat.agent.llm_proxy.requests import (
    messages_request_to_openai_payload,
    normalize_openai_response,
)
from tracecat.agent.llm_proxy.types import (
    AnthropicStreamEvent,
    NormalizedMessagesRequest,
    NormalizedResponse,
    NormalizedToolCall,
    ProviderHTTPRequest,
)


def _model_name(model: str) -> str:
    return model.split("/")[-1].lower()


def _is_openai_gpt_5_model(model: str) -> bool:
    model_name = _model_name(model)
    return model_name.startswith("gpt-5") and not model_name.startswith("gpt-5-chat")


def _is_openai_gpt_5_1_or_5_2_model(model: str) -> bool:
    model_name = _model_name(model)
    return model_name.startswith("gpt-5.1") or model_name.startswith("gpt-5.2")


def _is_openai_gpt_5_1_codex_max_model(model: str) -> bool:
    return _model_name(model) == "gpt-5.1-codex-max"


def _is_azure_gpt_5_1_model(model: str) -> bool:
    return _model_name(model).startswith("gpt-5.1")


def _drop_payload_keys(payload: dict[str, Any], *keys: str) -> None:
    for key in keys:
        payload.pop(key, None)


def _normalize_openai_token_params(payload: dict[str, Any], *, model: str) -> None:
    if "max_completion_tokens" in payload:
        payload.pop("max_tokens", None)
        return
    if _is_openai_gpt_5_model(model) and "max_tokens" in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens")


def _normalize_openai_reasoning_effort(
    payload: dict[str, Any],
    *,
    model: str,
    provider: str,
) -> None:
    reasoning_effort = payload.get("reasoning_effort")
    if reasoning_effort == "xhigh":
        if _is_openai_gpt_5_1_codex_max_model(model) or _model_name(model).startswith(
            "gpt-5.2"
        ):
            return
        if _is_openai_gpt_5_model(model):
            payload.pop("reasoning_effort", None)
        return

    if provider == "azure_openai" and reasoning_effort == "none":
        if not _is_azure_gpt_5_1_model(model):
            payload.pop("reasoning_effort", None)


def _normalize_openai_temperature(payload: dict[str, Any], *, model: str) -> None:
    temperature = payload.get("temperature")
    if temperature is None:
        return
    if not _is_openai_gpt_5_model(model):
        return
    reasoning_effort = payload.get("reasoning_effort")
    if _is_openai_gpt_5_1_or_5_2_model(model) and reasoning_effort in {None, "none"}:
        return
    if temperature != 1:
        payload.pop("temperature", None)


def _normalize_openai_unsupported_params(
    payload: dict[str, Any], *, model: str
) -> None:
    if not _is_openai_gpt_5_model(model):
        return
    _drop_payload_keys(
        payload,
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "stop",
    )


def _normalize_custom_model_provider_payload(payload: dict[str, Any]) -> None:
    if "max_completion_tokens" in payload and "max_tokens" not in payload:
        payload["max_tokens"] = payload.pop("max_completion_tokens")


def _normalize_openai_payload(
    payload: dict[str, Any],
    *,
    model: str,
    provider: str,
) -> None:
    _normalize_openai_token_params(payload, model=model)
    _normalize_openai_reasoning_effort(payload, model=model, provider=provider)
    _normalize_openai_temperature(payload, model=model)
    _normalize_openai_unsupported_params(payload, model=model)
    if provider == "custom-model-provider":
        _normalize_custom_model_provider_payload(payload)


def _sanitize_openai_usage(payload: dict[str, Any]) -> None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if key.endswith("_tokens") and value is None:
            usage[key] = 0


def _apply_tool_name_mapping(payload: dict[str, Any], mapping: dict[str, str]) -> None:
    if not mapping:
        return
    original_to_truncated = {
        original: truncated for truncated, original in mapping.items()
    }

    tools = payload.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function") if tool.get("type") == "function" else tool
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name in original_to_truncated:
                function["name"] = original_to_truncated[name]

    tool_choice = payload.get("tool_choice")
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name in original_to_truncated:
                function["name"] = original_to_truncated[name]

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict):
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name in original_to_truncated:
                function["name"] = original_to_truncated[name]


def _restore_tool_call_names(
    tool_calls: tuple[NormalizedToolCall, ...],
    mapping: dict[str, str],
) -> tuple[NormalizedToolCall, ...]:
    if not mapping or not tool_calls:
        return tool_calls
    return tuple(
        NormalizedToolCall(
            id=tool_call.id,
            name=restore_tool_name(tool_call.name, mapping) or tool_call.name,
            arguments=tool_call.arguments,
            type=tool_call.type,
        )
        for tool_call in tool_calls
    )


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


@dataclass(slots=True)
class OpenAIFamilyAdapter(AnthropicStreamingAdapter, ProviderRetryAdapter):
    """Adapter for OpenAI-compatible upstreams."""

    provider: str

    def prepare_request(
        self,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
    ) -> ProviderHTTPRequest:
        base_url = base_url_from_request(request, "https://api.openai.com")
        payload = messages_request_to_openai_payload(request)
        tool_name_mapping = create_tool_name_mapping(request.tools)
        _apply_tool_name_mapping(payload, tool_name_mapping)
        _normalize_openai_payload(payload, model=request.model, provider=self.provider)
        headers = {"Content-Type": "application/json"}

        if self.provider == "azure_openai":
            api_base = credentials.get("AZURE_API_BASE", base_url).rstrip("/")
            deployment = credentials.get("AZURE_DEPLOYMENT_NAME", request.model)
            api_version = credentials.get("AZURE_API_VERSION", request.api_version)
            if not api_version:
                raise ValueError("Azure OpenAI requires AZURE_API_VERSION")
            payload["model"] = deployment
            if api_key := credentials.get("AZURE_API_KEY"):
                headers["api-key"] = api_key
            elif ad_token := credentials.get("AZURE_AD_TOKEN"):
                headers["Authorization"] = f"Bearer {ad_token}"
            else:
                raise ValueError(
                    "Azure OpenAI requires AZURE_API_KEY or AZURE_AD_TOKEN"
                )
            return ProviderHTTPRequest(
                method="POST",
                url=f"{api_base}/openai/deployments/{deployment}/chat/completions?api-version={api_version}",
                headers=headers,
                json_body=payload,
                stream=request.stream,
            )

        if self.provider == "custom-model-provider":
            custom_base_url = credentials.get(
                "CUSTOM_MODEL_PROVIDER_BASE_URL", base_url
            )
            model_name = credentials.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME")
            if model_name:
                payload["model"] = model_name
            if api_key := credentials.get("CUSTOM_MODEL_PROVIDER_API_KEY"):
                headers["Authorization"] = f"Bearer {api_key}"
            return ProviderHTTPRequest(
                method="POST",
                url=urljoin(custom_base_url.rstrip("/") + "/", "/v1/chat/completions"),
                headers=headers,
                json_body=payload,
                stream=request.stream,
            )

        api_key = credentials.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI-compatible provider requires OPENAI_API_KEY")
        headers["Authorization"] = f"Bearer {api_key}"
        if explicit_base_url := credentials.get("OPENAI_BASE_URL"):
            base_url = explicit_base_url.rstrip("/")
        return ProviderHTTPRequest(
            method="POST",
            url=urljoin(base_url.rstrip("/") + "/", "/v1/chat/completions"),
            headers=headers,
            json_body=payload,
            stream=request.stream,
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
        del request, credentials
        if attempt > 0 or response.status_code not in {400, 422}:
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
        if response.status_code >= 400:
            provider_http_error(response, self.provider)
        payload = response.json()
        if isinstance(payload, dict):
            _sanitize_openai_usage(payload)
        normalized = normalize_openai_response(payload)
        tool_name_mapping = create_tool_name_mapping(request.tools)
        return NormalizedResponse(
            provider=self.provider,
            model=normalized.model,
            content=normalized.content,
            tool_calls=_restore_tool_call_names(
                normalized.tool_calls, tool_name_mapping
            ),
            finish_reason=normalized.finish_reason,
            usage=normalized.usage,
            raw=payload,
        )

    async def stream_anthropic(
        self,
        client: httpx.AsyncClient,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
    ) -> AsyncIterator[AnthropicStreamEvent]:
        outbound = self.prepare_request(request, credentials)
        tool_name_mapping = create_tool_name_mapping(request.tools)
        text_block_index: int | None = None
        next_content_index = 0
        message_started = False
        output_tokens = 0
        stop_reason: str | None = None
        tool_states: dict[int, OpenAIToolStreamState] = {}

        async with client.stream(
            outbound.method,
            outbound.url,
            headers=outbound.headers,
            content=outbound.body,
            json=outbound.json_body,
        ) as response:
            if response.status_code >= 400:
                await raise_stream_http_error(response, provider=self.provider)

            async for sse_event in iter_sse_events(response):
                if sse_event.data == "[DONE]":
                    break

                chunk = orjson.loads(sse_event.data)
                if not isinstance(chunk, dict):
                    continue

                usage = openai_stream_usage(chunk)
                output_tokens = usage["output_tokens"] or output_tokens
                if not message_started:
                    yield anthropic_message_start_event(
                        message_id=str(
                            chunk.get("id", f"tracecat-{self.provider}-stream")
                        ),
                        model=str(chunk.get("model", request.model)),
                        input_tokens=usage["input_tokens"],
                    )
                    message_started = True

                choices = chunk.get("choices")
                if not isinstance(choices, list):
                    continue

                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta")
                    if not isinstance(delta, dict):
                        delta = {}

                    content = delta.get("content")
                    if content:
                        if text_block_index is None:
                            text_block_index = next_content_index
                            next_content_index += 1
                            yield anthropic_text_block_start_event(text_block_index)
                        yield anthropic_text_delta_event(text_block_index, str(content))

                    tool_calls = delta.get("tool_calls")
                    if isinstance(tool_calls, list):
                        if text_block_index is not None:
                            yield anthropic_block_stop_event(text_block_index)
                            text_block_index = None

                        for tool_call in tool_calls:
                            if not isinstance(tool_call, dict):
                                continue
                            tool_index = int(tool_call.get("index", len(tool_states)))
                            tool_state = tool_states.get(tool_index)
                            if tool_state is None:
                                tool_state = OpenAIToolStreamState(
                                    content_index=next_content_index
                                )
                                tool_states[tool_index] = tool_state
                                next_content_index += 1

                            if tool_call_id := tool_call.get("id"):
                                tool_state.tool_call_id = str(tool_call_id)

                            function = tool_call.get("function")
                            if isinstance(function, dict):
                                if function_name := function.get("name"):
                                    tool_state.name = restore_tool_name(
                                        str(function_name),
                                        tool_name_mapping,
                                    )
                                if arguments := function.get("arguments"):
                                    if tool_state.started:
                                        yield anthropic_tool_delta_event(
                                            tool_state.content_index,
                                            str(arguments),
                                        )
                                    else:
                                        tool_state.pending_json.append(str(arguments))

                            if (
                                not tool_state.started
                                and tool_state.tool_call_id
                                and tool_state.name
                            ):
                                yield anthropic_tool_block_start_event(
                                    index=tool_state.content_index,
                                    tool_call_id=tool_state.tool_call_id,
                                    name=tool_state.name,
                                )
                                tool_state.started = True
                                for pending_json in tool_state.pending_json:
                                    yield anthropic_tool_delta_event(
                                        tool_state.content_index,
                                        pending_json,
                                    )
                                tool_state.pending_json.clear()

                    raw_finish_reason = choice.get("finish_reason")
                    if raw_finish_reason is None or isinstance(raw_finish_reason, str):
                        stop_reason = openai_finish_reason_to_anthropic(
                            raw_finish_reason
                        )

        if not message_started:
            yield anthropic_message_start_event(
                message_id=f"tracecat-{self.provider}-stream",
                model=request.model,
            )

        if text_block_index is not None:
            yield anthropic_block_stop_event(text_block_index)

        for tool_index in sorted(tool_states):
            tool_state = tool_states[tool_index]
            if not tool_state.started:
                yield anthropic_tool_block_start_event(
                    index=tool_state.content_index,
                    tool_call_id=tool_state.tool_call_id or "",
                    name=tool_state.name or "",
                )
                for pending_json in tool_state.pending_json:
                    yield anthropic_tool_delta_event(
                        tool_state.content_index,
                        pending_json,
                    )
            if not tool_state.stopped:
                yield anthropic_block_stop_event(tool_state.content_index)
                tool_state.stopped = True

        yield anthropic_message_delta_event(
            stop_reason=stop_reason,
            output_tokens=output_tokens,
        )
        yield AnthropicStreamEvent("message_stop", {"type": "message_stop"})


OpenAICompatibleAdapter = OpenAIFamilyAdapter

__all__ = [
    "OpenAIFamilyAdapter",
    "OpenAICompatibleAdapter",
]
