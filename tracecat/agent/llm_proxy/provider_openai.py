"""OpenAI-family provider adapters for the Tracecat-owned LLM proxy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import orjson

from tracecat.agent.llm_proxy.anthropic_compat import (
    anthropic_tool_to_openai_tool,
    create_tool_name_mapping,
    restore_tool_name,
    tool_choice_to_openai,
)
from tracecat.agent.llm_proxy.provider_common import (
    AnthropicStreamingAdapter,
    anthropic_block_stop_event,
    anthropic_message_delta_event,
    anthropic_message_start_event,
    anthropic_signature_delta_event,
    anthropic_text_block_start_event,
    anthropic_text_delta_event,
    anthropic_thinking_delta_event,
    anthropic_tool_block_start_event,
    anthropic_tool_delta_event,
    base_url_from_request,
    iter_sse_events,
    provider_http_error,
)
from tracecat.agent.llm_proxy.requests import (
    ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY,
)
from tracecat.agent.llm_proxy.types import (
    AnthropicStreamEvent,
    NormalizedMessagesRequest,
    NormalizedResponse,
    NormalizedToolCall,
    ProviderHTTPRequest,
)

_OPENAI_RESPONSES_SUFFIX = "/v1/responses"
_OPENAI_REASONING_SIGNATURE_PREFIX = "tcsig:v1:"
_DEFAULT_OPENAI_MAX_OUTPUT_TOKENS = 32000


def _openai_responses_url(base_url: str) -> str:
    """Build the responses URL, avoiding /v1 duplication."""
    base = base_url.rstrip("/")
    if base.endswith(_OPENAI_RESPONSES_SUFFIX):
        return base
    if base.endswith("/v1"):
        return f"{base}/responses"
    return f"{base}{_OPENAI_RESPONSES_SUFFIX}"


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
    # parallel_tool_calls is not part of the OpenAI spec and causes LiteLLM to
    # generate a malformed tool_choice when routing to Bedrock (missing the
    # required `type` field).  Drop it for custom providers since we cannot
    # control how the downstream proxy translates it.
    payload.pop("parallel_tool_calls", None)


def normalize_openai_chat_payload(
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


def _normalize_openai_responses_settings(
    settings: dict[str, Any],
    *,
    model: str,
    provider: str,
) -> None:
    _normalize_openai_token_params(settings, model=model)
    _normalize_openai_reasoning_effort(settings, model=model, provider=provider)
    _normalize_openai_temperature(settings, model=model)
    _normalize_openai_unsupported_params(settings, model=model)


def _sanitize_openai_usage(payload: dict[str, Any]) -> None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if key.endswith("_tokens") and value is None:
            usage[key] = 0


def _responses_reasoning_signature(encrypted_content: str) -> str:
    return f"{_OPENAI_REASONING_SIGNATURE_PREFIX}{encrypted_content}"


def _coerce_message_content_items(content: Any) -> list[Any]:
    if content is None:
        return []
    if isinstance(content, list):
        return list(content)
    return [content]


def _stringify_response_item_payload(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    try:
        return orjson.dumps(value).decode("utf-8")
    except TypeError:
        return str(value)


def _responses_text_items(parts: list[str], *, role: str) -> dict[str, Any]:
    text_type = "output_text" if role == "assistant" else "input_text"
    return {
        "type": "message",
        "role": role,
        "content": [{"type": text_type, "text": text} for text in parts],
    }


def _assistant_response_input_blocks(message: Any) -> list[Any]:
    metadata_blocks = message.metadata.get(ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY)
    if isinstance(metadata_blocks, list):
        return metadata_blocks
    return _coerce_message_content_items(message.content)


def _portable_tool_call_summary_text(*, name: str, arguments: Any) -> str:
    if rendered_arguments := _stringify_response_item_payload(arguments):
        return f"[Tool call] {name}: {rendered_arguments}"
    return f"[Tool call] {name}"


def _portable_tool_result_summary_text(message: Any) -> str | None:
    content = _stringify_response_item_payload(message.content).strip()
    if not content:
        return None
    tool_name = message.name or message.tool_call_id or "tool"
    return f"[Tool result] {tool_name}: {content}"


def _responses_input_items_from_assistant_message(
    message: Any,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pending_text: list[str] = []
    seen_tool_call_ids: set[str] = set()

    def flush_pending_text() -> None:
        if pending_text:
            items.append(_responses_text_items(pending_text.copy(), role="assistant"))
            pending_text.clear()

    for item in _assistant_response_input_blocks(message):
        if not isinstance(item, dict):
            pending_text.append(str(item))
            continue

        match item.get("type"):
            case "text":
                pending_text.append(str(item.get("text", "")))
            case "thinking":
                continue
            case "tool_use":
                if isinstance(tool_call_id := item.get("id"), str) and tool_call_id:
                    seen_tool_call_ids.add(tool_call_id)
                pending_text.append(
                    _portable_tool_call_summary_text(
                        name=str(item.get("name", "")),
                        arguments=item.get("input"),
                    )
                )
            case _:
                pending_text.append(str(item.get("text", item)))

    flush_pending_text()

    for tool_call in message.tool_calls:
        if tool_call.id in seen_tool_call_ids:
            continue
        items.append(
            _responses_text_items(
                [
                    _portable_tool_call_summary_text(
                        name=tool_call.name,
                        arguments=tool_call.arguments,
                    )
                ],
                role="assistant",
            )
        )

    return items


def _responses_input_items_from_message(message: Any) -> list[dict[str, Any]]:
    if message.role == "tool":
        if tool_result_text := _portable_tool_result_summary_text(message):
            return [_responses_text_items([tool_result_text], role="assistant")]
        return []

    if message.role == "assistant":
        return _responses_input_items_from_assistant_message(message)

    text_parts: list[str] = []
    for item in _coerce_message_content_items(message.content):
        if isinstance(item, dict) and item.get("type") == "text":
            text_parts.append(str(item.get("text", "")))
        else:
            text_parts.append(str(item))
    if not text_parts:
        return []
    return [_responses_text_items(text_parts, role=message.role)]


def _openai_responses_tool_definition(tool: dict[str, Any]) -> dict[str, Any]:
    openai_tool = anthropic_tool_to_openai_tool(dict(tool))
    if openai_tool.get("type") == "function" and isinstance(
        function := openai_tool.get("function"), dict
    ):
        return {"type": "function", **function}
    return openai_tool


def _openai_responses_tool_choice(tool_choice: Any) -> Any:
    openai_tool_choice = tool_choice_to_openai(tool_choice)
    if (
        isinstance(openai_tool_choice, dict)
        and openai_tool_choice.get("type") == "function"
        and isinstance(function := openai_tool_choice.get("function"), dict)
    ):
        return {"type": "function", "name": str(function.get("name", ""))}
    return openai_tool_choice


def _system_instructions(request: NormalizedMessagesRequest) -> str | None:
    instructions: list[str] = []
    for message in request.messages:
        if message.role != "system":
            continue
        for item in _coerce_message_content_items(message.content):
            if isinstance(item, dict) and item.get("type") == "text":
                instructions.append(str(item.get("text", "")))
            else:
                instructions.append(str(item))
    if not instructions:
        return None
    return "\n\n".join(instructions)


def _openai_responses_payload(request: NormalizedMessagesRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model,
        "input": [
            item
            for message in request.messages
            if message.role != "system"
            for item in _responses_input_items_from_message(message)
        ],
        "stream": request.stream,
    }
    if instructions := _system_instructions(request):
        payload["instructions"] = instructions
    if request.tools:
        payload["tools"] = [
            _openai_responses_tool_definition(tool) for tool in request.tools
        ]
    if request.tool_choice is not None and request.tools:
        payload["tool_choice"] = _openai_responses_tool_choice(request.tool_choice)

    model_settings = dict(request.model_settings)
    _normalize_openai_responses_settings(
        model_settings,
        model=request.model,
        provider=request.provider,
    )

    if (reasoning_effort := model_settings.pop("reasoning_effort", None)) is not None:
        payload["reasoning"] = {
            "effort": reasoning_effort,
            "summary": "auto",
        }
    if (
        max_completion_tokens := model_settings.pop("max_completion_tokens", None)
    ) is not None:
        payload["max_output_tokens"] = max_completion_tokens
    elif (max_tokens := model_settings.pop("max_tokens", None)) is not None:
        payload["max_output_tokens"] = max_tokens

    payload.update(model_settings)
    return payload


def _parse_openai_json_value(value: Any) -> Any:
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        try:
            return orjson.loads(value)
        except orjson.JSONDecodeError:
            return value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        try:
            return orjson.loads(value)
        except orjson.JSONDecodeError:
            return value
    return value


def _openai_responses_usage(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": int(usage.get("input_tokens", usage.get("prompt_tokens", 0))),
        "output_tokens": int(
            usage.get("output_tokens", usage.get("completion_tokens", 0))
        ),
    }


def _normalized_openai_responses_content(payload: dict[str, Any]) -> Any:
    output = payload.get("output")
    if not isinstance(output, list):
        return None

    content_blocks: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "reasoning":
            thinking = "".join(
                str(part.get("text", ""))
                for part in item.get("content", [])
                if isinstance(part, dict) and part.get("type") == "reasoning_text"
            )
            block: dict[str, Any] = {"type": "thinking", "thinking": thinking}
            if isinstance(reasoning_id := item.get("id"), str) and reasoning_id:
                block["id"] = reasoning_id
            if isinstance(summary := item.get("summary"), list):
                block["summary"] = [
                    {
                        "type": str(part.get("type", "summary_text")),
                        "text": str(part.get("text", "")),
                    }
                    for part in summary
                    if isinstance(part, dict)
                ]
            if isinstance(encrypted_content := item.get("encrypted_content"), str):
                block["signature"] = _responses_reasoning_signature(encrypted_content)
            content_blocks.append(block)
            continue
        if item_type == "message":
            for part in item.get("content", []):
                if not isinstance(part, dict):
                    content_blocks.append({"type": "text", "text": str(part)})
                    continue
                if part.get("type") == "output_text":
                    content_blocks.append(
                        {"type": "text", "text": str(part.get("text", ""))}
                    )

    if not content_blocks:
        return None

    has_non_text_blocks = any(block.get("type") != "text" for block in content_blocks)
    if not has_non_text_blocks:
        return "".join(str(block.get("text", "")) for block in content_blocks)
    return content_blocks


def _normalize_openai_responses_response(payload: dict[str, Any]) -> NormalizedResponse:
    output = payload.get("output")
    tool_calls: list[NormalizedToolCall] = []
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            tool_calls.append(
                NormalizedToolCall(
                    id=str(item.get("call_id", item.get("id", ""))),
                    name=str(item.get("name", "")),
                    arguments=_parse_openai_json_value(item.get("arguments")),
                )
            )

    finish_reason = "tool_use" if tool_calls else "end_turn"
    return NormalizedResponse(
        provider="openai",
        model=str(payload.get("model", "")),
        content=_normalized_openai_responses_content(payload),
        tool_calls=tuple(tool_calls),
        finish_reason=finish_reason,
        usage=_openai_responses_usage(payload),
        raw=payload,
    )


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
            function = tool.get("function") if tool.get("function") else tool
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name in original_to_truncated:
                function["name"] = original_to_truncated[name]

    tool_choice = payload.get("tool_choice")
    if isinstance(tool_choice, dict):
        function = (
            tool_choice.get("function") if tool_choice.get("function") else tool_choice
        )
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name in original_to_truncated:
                function["name"] = original_to_truncated[name]

    messages = payload.get("messages")
    if isinstance(messages, list):
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

    input_items = payload.get("input")
    if not isinstance(input_items, list):
        return
    for item in input_items:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        name = item.get("name")
        if isinstance(name, str) and name in original_to_truncated:
            item["name"] = original_to_truncated[name]


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


@dataclass(slots=True)
class OpenAIFamilyAdapter(AnthropicStreamingAdapter):
    """Adapter for OpenAI-compatible upstreams."""

    provider: str

    def _prepare_responses_request(
        self,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
        *,
        base_url: str,
    ) -> ProviderHTTPRequest:
        payload = _openai_responses_payload(request)
        tool_name_mapping = create_tool_name_mapping(request.tools)
        _apply_tool_name_mapping(payload, tool_name_mapping)
        headers = {"Content-Type": "application/json"}

        if self.provider == "custom-model-provider":
            custom_base_url = credentials.get(
                "CUSTOM_MODEL_PROVIDER_BASE_URL", base_url
            )
            if model_name := credentials.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME"):
                payload["model"] = model_name
            if api_key := credentials.get("CUSTOM_MODEL_PROVIDER_API_KEY"):
                headers["Authorization"] = f"Bearer {api_key}"
            target_url = _openai_responses_url(custom_base_url)
            return ProviderHTTPRequest(
                method="POST",
                url=target_url,
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
        target_url = _openai_responses_url(base_url)
        return ProviderHTTPRequest(
            method="POST",
            url=target_url,
            headers=headers,
            json_body=payload,
            stream=request.stream,
        )

    def prepare_request(
        self,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
    ) -> ProviderHTTPRequest:
        base_url = base_url_from_request(request, "https://api.openai.com")
        return self._prepare_responses_request(
            request,
            credentials,
            base_url=base_url,
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
        if not (isinstance(payload, dict) and isinstance(payload.get("output"), list)):
            raise RuntimeError(
                f"{self.provider} provider returned a non-Responses payload"
            )
        normalized = _normalize_openai_responses_response(payload)
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
        async for event in self._stream_openai_responses(client, request, outbound):
            yield event

    async def _stream_openai_responses(
        self,
        client: httpx.AsyncClient,
        request: NormalizedMessagesRequest,
        outbound: ProviderHTTPRequest,
    ) -> AsyncIterator[AnthropicStreamEvent]:
        tool_name_mapping = create_tool_name_mapping(request.tools)
        thinking_indices: dict[str, int] = {}
        text_indices: dict[str, int] = {}
        started_thinking_ids: set[str] = set()
        started_text_ids: set[str] = set()
        next_content_index = 0
        message_started = False
        output_tokens = 0
        stop_reason = "end_turn"

        def allocate_content_index(
            state: dict[str, int],
            item_id: str,
            output_index: Any = None,
        ) -> int:
            nonlocal next_content_index
            if item_id in state:
                return state[item_id]
            if isinstance(output_index, int):
                content_index = output_index
                if content_index >= next_content_index:
                    next_content_index = content_index + 1
            else:
                content_index = next_content_index
                next_content_index += 1
            state[item_id] = content_index
            return content_index

        async with client.stream(
            outbound.method,
            outbound.url,
            headers=outbound.headers,
            content=outbound.body,
            json=outbound.json_body,
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                detail = body.decode("utf-8", errors="ignore")[:512]
                raise RuntimeError(
                    f"{self.provider} provider error: {response.status_code} {detail}"
                )

            async for sse_event in iter_sse_events(response):
                if sse_event.data == "[DONE]":
                    break

                chunk = orjson.loads(sse_event.data)
                if not isinstance(chunk, dict):
                    continue

                event_name = sse_event.event or ""
                response_payload = chunk.get("response")
                if isinstance(response_payload, dict):
                    usage = _openai_responses_usage(response_payload)
                    output_tokens = usage["output_tokens"] or output_tokens
                    if not message_started:
                        yield anthropic_message_start_event(
                            message_id=str(
                                response_payload.get(
                                    "id", f"tracecat-{self.provider}-stream"
                                )
                            ),
                            model=str(response_payload.get("model", request.model)),
                            input_tokens=usage["input_tokens"],
                        )
                        message_started = True
                    continue

                if event_name == "response.reasoning_text.delta":
                    item_id = str(chunk.get("item_id", ""))
                    content_index = allocate_content_index(
                        thinking_indices,
                        item_id,
                        chunk.get("output_index"),
                    )
                    if item_id not in started_thinking_ids:
                        yield AnthropicStreamEvent(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": content_index,
                                "content_block": {
                                    "type": "thinking",
                                    "thinking": "",
                                    "id": item_id,
                                },
                            },
                        )
                        started_thinking_ids.add(item_id)
                    if isinstance(delta := chunk.get("delta"), str) and delta:
                        yield anthropic_thinking_delta_event(content_index, delta)
                    continue

                if event_name == "response.output_text.delta":
                    item_id = str(chunk.get("item_id", ""))
                    content_index = allocate_content_index(
                        text_indices,
                        item_id,
                        chunk.get("output_index"),
                    )
                    if item_id not in started_text_ids:
                        yield anthropic_text_block_start_event(content_index)
                        started_text_ids.add(item_id)
                    if isinstance(delta := chunk.get("delta"), str) and delta:
                        yield anthropic_text_delta_event(content_index, delta)
                    continue

                if event_name != "response.output_item.done":
                    continue

                item = chunk.get("item")
                if not isinstance(item, dict):
                    continue

                item_type = item.get("type")
                if item_type == "reasoning":
                    item_id = str(item.get("id", ""))
                    content_index = allocate_content_index(thinking_indices, item_id)
                    had_started_thinking = item_id in started_thinking_ids
                    reasoning_text = "".join(
                        str(part.get("text", ""))
                        for part in item.get("content", [])
                        if isinstance(part, dict)
                        and part.get("type") == "reasoning_text"
                    )
                    summary = item.get("summary")
                    summary_items = (
                        [
                            {
                                "type": str(part.get("type", "summary_text")),
                                "text": str(part.get("text", "")),
                            }
                            for part in summary
                            if isinstance(part, dict)
                        ]
                        if isinstance(summary, list)
                        else []
                    )
                    if not summary_items:
                        summary_items = [
                            {"type": "summary_text", "text": reasoning_text}
                        ]
                    if not had_started_thinking:
                        yield AnthropicStreamEvent(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": content_index,
                                "content_block": {
                                    "type": "thinking",
                                    "thinking": "",
                                    "id": item_id,
                                    "summary": summary_items,
                                },
                            },
                        )
                        started_thinking_ids.add(item_id)
                    if reasoning_text and not had_started_thinking:
                        yield anthropic_thinking_delta_event(
                            content_index,
                            reasoning_text,
                        )
                    if isinstance(
                        encrypted_content := item.get("encrypted_content"), str
                    ):
                        yield anthropic_signature_delta_event(
                            content_index,
                            _responses_reasoning_signature(encrypted_content),
                        )
                    yield anthropic_block_stop_event(content_index)
                    continue

                if item_type == "message":
                    item_id = str(item.get("id", ""))
                    content_index = allocate_content_index(text_indices, item_id)
                    had_started_text = item_id in started_text_ids
                    if not had_started_text:
                        yield anthropic_text_block_start_event(content_index)
                        started_text_ids.add(item_id)
                    for part in item.get("content", []):
                        if (
                            isinstance(part, dict)
                            and part.get("type") == "output_text"
                            and isinstance(text := part.get("text"), str)
                            and text
                            and not had_started_text
                        ):
                            yield anthropic_text_delta_event(content_index, text)
                    yield anthropic_block_stop_event(content_index)
                    continue

                if item_type == "function_call":
                    stop_reason = "tool_use"
                    tool_call_id = str(item.get("call_id", item.get("id", "")))
                    tool_name = restore_tool_name(
                        str(item.get("name", "")),
                        tool_name_mapping,
                    )
                    content_index = next_content_index
                    next_content_index += 1
                    yield anthropic_tool_block_start_event(
                        index=content_index,
                        tool_call_id=tool_call_id,
                        name=tool_name or "",
                    )
                    if arguments := item.get("arguments"):
                        yield anthropic_tool_delta_event(
                            content_index,
                            str(arguments),
                        )
                    yield anthropic_block_stop_event(content_index)

        if not message_started:
            yield anthropic_message_start_event(
                message_id=f"tracecat-{self.provider}-stream",
                model=request.model,
            )

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
