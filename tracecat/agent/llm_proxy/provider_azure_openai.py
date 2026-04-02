"""Azure OpenAI chat-completions adapter for the Tracecat-owned LLM proxy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, TypedDict

import httpx
import orjson

from tracecat.agent.llm_proxy.anthropic_compat import (
    apply_tool_name_mapping,
    create_tool_name_mapping,
    restore_tool_call_names,
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
    anthropic_thinking_block_start_event,
    anthropic_thinking_delta_event,
    anthropic_tool_block_start_event,
    anthropic_tool_delta_event,
    base_url_from_request,
    iter_sse_events,
    openai_finish_reason_to_anthropic,
    openai_stream_usage,
    provider_http_error,
    raise_stream_http_error,
)
from tracecat.agent.llm_proxy.provider_openai import (
    _sanitize_openai_usage,
    normalize_openai_payload,
)
from tracecat.agent.llm_proxy.requests import (
    messages_request_to_openai_payload,
    normalize_openai_response,
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


def _chat_reasoning_text(message: dict[str, Any]) -> str | None:
    reasoning = message.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        return reasoning
    reasoning_content = message.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content:
        return reasoning_content
    return None


def _normalized_chat_response_content(message: dict[str, Any]) -> Any:
    content = message.get("content")
    if not (reasoning := _chat_reasoning_text(message)):
        return content

    blocks: list[dict[str, Any]] = [{"type": "thinking", "thinking": reasoning}]
    if content:
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    blocks.append(dict(item))
                else:
                    blocks.append({"type": "text", "text": str(item)})
        else:
            blocks.append({"type": "text", "text": str(content)})
    return blocks


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
        if response.status_code >= 400:
            provider_http_error(response, self.provider)
        payload = response.json()
        if isinstance(payload, dict):
            _sanitize_openai_usage(payload)
        normalized = normalize_openai_response(payload)
        if isinstance(payload, dict) and isinstance(
            choice_message := ((payload.get("choices") or [{}])[0]).get("message"),
            dict,
        ):
            normalized = NormalizedResponse(
                provider=normalized.provider,
                model=normalized.model,
                content=_normalized_chat_response_content(choice_message),
                tool_calls=normalized.tool_calls,
                finish_reason=normalized.finish_reason,
                usage=normalized.usage,
                raw=normalized.raw,
            )
        tool_name_mapping = create_tool_name_mapping(request.tools)
        return NormalizedResponse(
            provider=self.provider,
            model=normalized.model,
            content=normalized.content,
            tool_calls=restore_tool_call_names(
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
        thinking_block_index: int | None = None
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

                    reasoning = delta.get("reasoning")
                    if reasoning:
                        if text_block_index is not None:
                            yield anthropic_block_stop_event(text_block_index)
                            text_block_index = None
                        if thinking_block_index is None:
                            thinking_block_index = next_content_index
                            next_content_index += 1
                            yield anthropic_thinking_block_start_event(
                                thinking_block_index
                            )
                        yield anthropic_thinking_delta_event(
                            thinking_block_index,
                            str(reasoning),
                        )

                    content = delta.get("content")
                    if content:
                        if thinking_block_index is not None:
                            yield anthropic_block_stop_event(thinking_block_index)
                            thinking_block_index = None
                        if text_block_index is None:
                            text_block_index = next_content_index
                            next_content_index += 1
                            yield anthropic_text_block_start_event(text_block_index)
                        yield anthropic_text_delta_event(text_block_index, str(content))

                    tool_calls = delta.get("tool_calls")
                    if isinstance(tool_calls, list):
                        if thinking_block_index is not None:
                            yield anthropic_block_stop_event(thinking_block_index)
                            thinking_block_index = None
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
        if thinking_block_index is not None:
            yield anthropic_block_stop_event(thinking_block_index)

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


__all__ = ["AzureOpenAIAdapter"]
