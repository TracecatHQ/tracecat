"""OpenAI-family provider adapters for the Tracecat-owned LLM proxy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import orjson

from tracecat.agent.llm_proxy.content_blocks import (
    ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY,
)
from tracecat.agent.llm_proxy.provider_common import (
    AnthropicStreamingAdapter,
    OpenAIToolStreamState,
    ProviderAdapter,
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
from tracecat.agent.llm_proxy.requests import messages_request_to_openai_payload
from tracecat.agent.llm_proxy.response_rendering import normalize_openai_response
from tracecat.agent.llm_proxy.tool_compat import (
    anthropic_tool_to_openai_tool,
    apply_tool_name_mapping,
    create_tool_name_mapping,
    restore_tool_call_names,
    restore_tool_name,
    tool_choice_to_openai,
)
from tracecat.agent.llm_proxy.types import (
    AnthropicStreamEvent,
    NormalizedMessagesRequest,
    NormalizedResponse,
    NormalizedToolCall,
    ProviderHTTPRequest,
)

_OPENAI_RESPONSES_SUFFIX = "/v1/responses"
_OPENAI_CHAT_COMPLETIONS_SUFFIX = "/v1/chat/completions"


def _openai_responses_url(base_url: str) -> str:
    """Build the responses URL, avoiding /v1 duplication."""
    base = base_url.rstrip("/")
    if base.endswith(_OPENAI_RESPONSES_SUFFIX):
        return base
    if base.endswith("/v1"):
        return f"{base}/responses"
    return f"{base}{_OPENAI_RESPONSES_SUFFIX}"


def _openai_chat_completions_url(base_url: str) -> str:
    """Build the chat-completions URL, avoiding /v1 duplication."""
    base = base_url.rstrip("/")
    if base.endswith(_OPENAI_CHAT_COMPLETIONS_SUFFIX):
        return base
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}{_OPENAI_CHAT_COMPLETIONS_SUFFIX}"


# ---------------------------------------------------------------------------
# Payload normalization for OpenAI model quirks
# ---------------------------------------------------------------------------


# GPT-5 family capability flags by model prefix (longest prefix wins).
# xhigh: supports reasoning_effort="xhigh"
# temp_with_reasoning_off: allows temperature when reasoning_effort is None/"none"
_GPT5_CAPABILITIES: dict[str, dict[str, bool]] = {
    "gpt-5.1-codex-max": {"xhigh": True, "temp_with_reasoning_off": True},
    "gpt-5.2": {"xhigh": True, "temp_with_reasoning_off": True},
    "gpt-5.3": {"xhigh": True, "temp_with_reasoning_off": True},
    "gpt-5.4": {"xhigh": True, "temp_with_reasoning_off": True},
    "gpt-5.1": {"xhigh": False, "temp_with_reasoning_off": True},
    "gpt-5": {"xhigh": False, "temp_with_reasoning_off": False},
}


def _gpt5_caps(model_name: str) -> dict[str, bool] | None:
    """Return capability flags for a GPT-5 model, or None if not GPT-5."""
    for prefix, caps in _GPT5_CAPABILITIES.items():
        if model_name.startswith(prefix):
            return caps
    return None


def normalize_openai_payload(
    payload: dict[str, Any],
    *,
    model: str,
    provider: str | None = None,
) -> None:
    """Normalize payload params for OpenAI model compatibility."""
    model_name = model.split("/")[-1].lower()
    caps = _gpt5_caps(model_name)

    # --- Token param naming ---
    if "max_completion_tokens" in payload:
        payload.pop("max_tokens", None)
    elif caps and "max_tokens" in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens")

    if caps:
        # --- Reasoning effort: xhigh only on supported models ---
        if payload.get("reasoning_effort") == "xhigh" and not caps["xhigh"]:
            payload.pop("reasoning_effort", None)

        # --- Temperature: restricted when reasoning is active ---
        temperature = payload.get("temperature")
        if temperature is not None and temperature != 1:
            reasoning_effort = payload.get("reasoning_effort")
            can_use_temp = caps["temp_with_reasoning_off"] and reasoning_effort in {
                None,
                "none",
            }
            if not can_use_temp:
                payload.pop("temperature", None)

        # --- Drop unsupported sampling params ---
        for key in ("top_p", "presence_penalty", "frequency_penalty", "stop"):
            payload.pop(key, None)

    # --- Custom model provider: downgrade to max_tokens ---
    if provider == "custom-model-provider":
        if "max_completion_tokens" in payload and "max_tokens" not in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        payload.pop("parallel_tool_calls", None)


def _sanitize_openai_usage(payload: dict[str, Any]) -> None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if key.endswith("_tokens") and value is None:
            usage[key] = 0


# ---------------------------------------------------------------------------
# Responses API payload building
# ---------------------------------------------------------------------------


def _stringify(value: Any) -> str:
    """Coerce a value to a string, preferring JSON for structured data."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    try:
        return orjson.dumps(value).decode("utf-8")
    except TypeError:
        return str(value)


def _tool_call_summary(*, name: str, arguments: Any) -> str:
    if rendered := _stringify(arguments):
        return f"[Tool call] {name}: {rendered}"
    return f"[Tool call] {name}"


def _text_message(parts: list[str], *, role: str) -> dict[str, Any]:
    """Build a Responses API message item with text content."""
    text_type = "output_text" if role == "assistant" else "input_text"
    return {
        "type": "message",
        "role": role,
        "content": [{"type": text_type, "text": text} for text in parts],
    }


def _chat_reasoning_text(message: dict[str, Any]) -> str | None:
    reasoning = message.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        return reasoning
    reasoning_content = message.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content:
        return reasoning_content
    return None


def _coerce_openai_content_value(value: Any) -> str:
    """Coerce a value into a string for chat-completions history replay."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    try:
        return orjson.dumps(value).decode("utf-8")
    except TypeError:
        return str(value)


def _coerce_openai_content_block(block: Any) -> dict[str, Any]:
    """Convert non-text content blocks to text blocks for chat completions."""
    if not isinstance(block, dict):
        return {"type": "text", "text": str(block)}

    block_type = block.get("type")
    if block_type == "text":
        return {"type": "text", "text": str(block.get("text", ""))}
    if block_type == "thinking":
        return {"type": "text", "text": str(block.get("thinking", ""))}
    if block_type == "tool_use":
        return {
            "type": "text",
            "text": _coerce_openai_content_value(
                block.get("input", {"name": block.get("name", "")})
            ),
        }
    return {"type": "text", "text": _coerce_openai_content_value(block)}


def _coerce_openai_message_content(content: Any) -> Any:
    """Return chat-compatible assistant content for history replay."""
    if isinstance(content, list):
        return [_coerce_openai_content_block(item) for item in content]
    if isinstance(content, dict):
        return [_coerce_openai_content_block(content)]
    if isinstance(content, (str, bytes)):
        return content
    if content is None:
        return content
    return str(content)


def _sanitize_chat_completions_payload_for_history_replay(
    payload: dict[str, Any],
) -> None:
    """Replace Anthropic-only assistant blocks with chat-compatible content."""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return

    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        message["content"] = _coerce_openai_message_content(message.get("content"))


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


# ---------------------------------------------------------------------------
# Shared chat-completions response parsing
# ---------------------------------------------------------------------------


async def _parse_chat_completions_response(
    response: httpx.Response,
    request: NormalizedMessagesRequest,
    *,
    provider: str,
) -> NormalizedResponse:
    """Parse a chat-completions response (shared between OpenAI and Azure)."""
    if response.status_code >= 400:
        provider_http_error(response, provider)
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
        provider=provider,
        model=normalized.model,
        content=normalized.content,
        tool_calls=restore_tool_call_names(normalized.tool_calls, tool_name_mapping),
        finish_reason=normalized.finish_reason,
        usage=normalized.usage,
        raw=payload,
    )


# ---------------------------------------------------------------------------
# Responses API payload building
# ---------------------------------------------------------------------------


def _responses_input_items_from_message(message: Any) -> list[dict[str, Any]]:
    """Convert a NormalizedMessage into Responses API input items."""
    if message.role == "tool":
        content = _stringify(message.content).strip()
        if not content:
            return []
        tool_name = message.name or message.tool_call_id or "tool"
        return [
            _text_message([f"[Tool result] {tool_name}: {content}"], role="assistant")
        ]

    if message.role == "assistant":
        return _responses_input_items_from_assistant(message)

    # user / system
    text_parts: list[str] = []
    content = message.content
    items = (
        [] if content is None else (content if isinstance(content, list) else [content])
    )
    for item in items:
        if isinstance(item, dict) and item.get("type") == "text":
            text_parts.append(str(item.get("text", "")))
        else:
            text_parts.append(str(item))
    if not text_parts:
        return []
    return [_text_message(text_parts, role=message.role)]


def _responses_input_items_from_assistant(message: Any) -> list[dict[str, Any]]:
    """Convert an assistant NormalizedMessage into Responses API input items."""
    items: list[dict[str, Any]] = []
    pending_text: list[str] = []
    seen_tool_call_ids: set[str] = set()

    # Prefer stashed Anthropic content blocks over raw content
    metadata_blocks = message.metadata.get(ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY)
    content_items = (
        metadata_blocks
        if isinstance(metadata_blocks, list)
        else (
            list(message.content)
            if isinstance(message.content, list)
            else ([] if message.content is None else [message.content])
        )
    )

    def flush() -> None:
        if pending_text:
            items.append(_text_message(pending_text.copy(), role="assistant"))
            pending_text.clear()

    for item in content_items:
        if not isinstance(item, dict):
            pending_text.append(str(item))
            continue

        match item.get("type"):
            case "text":
                pending_text.append(str(item.get("text", "")))
            case "thinking":
                continue
            case "tool_use":
                if isinstance(tc_id := item.get("id"), str) and tc_id:
                    seen_tool_call_ids.add(tc_id)
                pending_text.append(
                    _tool_call_summary(
                        name=str(item.get("name", "")),
                        arguments=item.get("input"),
                    )
                )
            case "tool_result":
                continue
            case _:
                pending_text.append(str(item.get("text", item)))

    flush()

    # Emit any tool calls not already covered by content blocks
    for tool_call in message.tool_calls:
        if tool_call.id in seen_tool_call_ids:
            continue
        items.append(
            _text_message(
                [
                    _tool_call_summary(
                        name=tool_call.name, arguments=tool_call.arguments
                    )
                ],
                role="assistant",
            )
        )

    return items


def _responses_tool_definition(tool: dict[str, Any]) -> dict[str, Any]:
    openai_tool = anthropic_tool_to_openai_tool(dict(tool))
    if openai_tool.get("type") == "function" and isinstance(
        function := openai_tool.get("function"), dict
    ):
        return {"type": "function", **function}
    return openai_tool


def _responses_tool_choice(tool_choice: Any) -> Any:
    openai_tool_choice = tool_choice_to_openai(tool_choice)
    if (
        isinstance(openai_tool_choice, dict)
        and openai_tool_choice.get("type") == "function"
        and isinstance(function := openai_tool_choice.get("function"), dict)
    ):
        return {"type": "function", "name": str(function.get("name", ""))}
    return openai_tool_choice


def _openai_responses_payload(request: NormalizedMessagesRequest) -> dict[str, Any]:
    """Build the full Responses API request payload."""
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

    # System instructions
    instructions_parts: list[str] = []
    for message in request.messages:
        if message.role != "system":
            continue
        content = message.content
        items = (
            []
            if content is None
            else (content if isinstance(content, list) else [content])
        )
        for item in items:
            if isinstance(item, dict) and item.get("type") == "text":
                instructions_parts.append(str(item.get("text", "")))
            else:
                instructions_parts.append(str(item))
    if instructions_parts:
        payload["instructions"] = "\n\n".join(instructions_parts)

    if request.tools:
        payload["tools"] = [_responses_tool_definition(tool) for tool in request.tools]
    if request.tool_choice is not None and request.tools:
        payload["tool_choice"] = _responses_tool_choice(request.tool_choice)

    model_settings = dict(request.model_settings)
    normalize_openai_payload(
        model_settings,
        model=request.model,
        provider=request.provider,
    )

    if (reasoning_effort := model_settings.pop("reasoning_effort", None)) is not None:
        payload["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
    if (
        max_completion_tokens := model_settings.pop("max_completion_tokens", None)
    ) is not None:
        payload["max_output_tokens"] = max_completion_tokens
    elif (max_tokens := model_settings.pop("max_tokens", None)) is not None:
        payload["max_output_tokens"] = max_tokens

    payload.update(model_settings)
    return payload


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_json_value(value: Any) -> Any:
    """Parse a JSON value, passing through already-decoded types."""
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


def _responses_usage(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": int(usage.get("input_tokens", usage.get("prompt_tokens", 0))),
        "output_tokens": int(
            usage.get("output_tokens", usage.get("completion_tokens", 0))
        ),
    }


def _normalized_responses_content(payload: dict[str, Any]) -> Any:
    """Extract content blocks from an OpenAI Responses API payload."""
    output = payload.get("output")
    if not isinstance(output, list):
        return None

    content_blocks: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "reasoning":
            # Custom models return reasoning_text in content;
            # OpenAI models return an encrypted blob + human-readable summary
            thinking = "".join(
                str(part.get("text", ""))
                for part in item.get("content", [])
                if isinstance(part, dict) and part.get("type") == "reasoning_text"
            )
            if not thinking and isinstance(summary := item.get("summary"), list):
                thinking = "".join(
                    str(part.get("text", ""))
                    for part in summary
                    if isinstance(part, dict)
                )
            content_blocks.append({"type": "thinking", "thinking": thinking})
        elif item_type == "message":
            for part in item.get("content", []):
                if not isinstance(part, dict):
                    content_blocks.append({"type": "text", "text": str(part)})
                elif part.get("type") == "output_text":
                    content_blocks.append(
                        {"type": "text", "text": str(part.get("text", ""))}
                    )

    if not content_blocks:
        return None

    if all(block.get("type") == "text" for block in content_blocks):
        return "".join(str(block.get("text", "")) for block in content_blocks)
    return content_blocks


def _normalize_responses_response(payload: dict[str, Any]) -> NormalizedResponse:
    """Normalize an OpenAI Responses API payload into a NormalizedResponse."""
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
                    arguments=_parse_json_value(item.get("arguments")),
                )
            )

    # Map Responses API status to Anthropic-style finish reason.
    if tool_calls:
        finish_reason = "tool_use"
    else:
        match payload.get("status"):
            case "incomplete":
                finish_reason = "max_tokens"
            case _:
                finish_reason = "end_turn"

    return NormalizedResponse(
        provider="openai",
        model=str(payload.get("model", "")),
        content=_normalized_responses_content(payload),
        tool_calls=tuple(tool_calls),
        finish_reason=finish_reason,
        usage=_responses_usage(payload),
        raw=payload,
    )


def _openai_chat_payload(request: NormalizedMessagesRequest) -> dict[str, Any]:
    """Build the full chat-completions request payload."""
    payload = messages_request_to_openai_payload(request)
    _sanitize_chat_completions_payload_for_history_replay(payload)
    normalize_openai_payload(
        payload,
        model=request.model,
        provider=request.provider,
    )
    return payload


# ---------------------------------------------------------------------------
# Shared streaming for chat-completions
# ---------------------------------------------------------------------------


async def _stream_openai_chat_completions(
    client: httpx.AsyncClient,
    request: NormalizedMessagesRequest,
    outbound: ProviderHTTPRequest,
    *,
    provider: str,
) -> AsyncIterator[AnthropicStreamEvent]:
    """Stream OpenAI chat-completions response (shared between adapters)."""
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
            await raise_stream_http_error(response, provider=provider)

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
                    message_id=str(chunk.get("id", f"tracecat-{provider}-stream")),
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

                reasoning = _chat_reasoning_text(delta)
                if reasoning:
                    if text_block_index is not None:
                        yield anthropic_block_stop_event(text_block_index)
                        text_block_index = None
                    if thinking_block_index is None:
                        thinking_block_index = next_content_index
                        next_content_index += 1
                        yield anthropic_thinking_block_start_event(thinking_block_index)
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
                    stop_reason = openai_finish_reason_to_anthropic(raw_finish_reason)

    if not message_started:
        yield anthropic_message_start_event(
            message_id=f"tracecat-{provider}-stream",
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


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OpenAIFamilyAdapter(ProviderAdapter, AnthropicStreamingAdapter):
    """Adapter for OpenAI-compatible upstreams."""

    provider: str

    def _prepare_custom_provider_chat_request(
        self,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
        *,
        base_url: str,
    ) -> ProviderHTTPRequest:
        payload = _openai_chat_payload(request)
        tool_name_mapping = create_tool_name_mapping(request.tools)
        apply_tool_name_mapping(payload, tool_name_mapping)
        custom_base_url = credentials.get("CUSTOM_MODEL_PROVIDER_BASE_URL", base_url)
        headers = {"Content-Type": "application/json"}
        if model_name := credentials.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME"):
            payload["model"] = model_name
        if api_key := credentials.get("CUSTOM_MODEL_PROVIDER_API_KEY"):
            headers["Authorization"] = f"Bearer {api_key}"
        return ProviderHTTPRequest(
            method="POST",
            url=_openai_chat_completions_url(custom_base_url),
            headers=headers,
            json_body=payload,
            stream=request.stream,
        )

    def _prepare_responses_request(
        self,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
        *,
        base_url: str,
    ) -> ProviderHTTPRequest:
        if self.provider == "custom-model-provider":
            return self._prepare_custom_provider_chat_request(
                request,
                credentials,
                base_url=base_url,
            )

        payload = _openai_responses_payload(request)
        tool_name_mapping = create_tool_name_mapping(request.tools)
        apply_tool_name_mapping(payload, tool_name_mapping)
        headers = {"Content-Type": "application/json"}

        api_key = credentials.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI-compatible provider requires OPENAI_API_KEY")
        headers["Authorization"] = f"Bearer {api_key}"
        if explicit_base_url := credentials.get("OPENAI_BASE_URL"):
            base_url = explicit_base_url.rstrip("/")
        return ProviderHTTPRequest(
            method="POST",
            url=_openai_responses_url(base_url),
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
        if self.provider == "custom-model-provider":
            return await _parse_chat_completions_response(
                response, request, provider=self.provider
            )

        if response.status_code >= 400:
            provider_http_error(response, self.provider)
        payload = response.json()
        if isinstance(payload, dict):
            _sanitize_openai_usage(payload)
        if not (isinstance(payload, dict) and isinstance(payload.get("output"), list)):
            raise RuntimeError(
                f"{self.provider} provider returned a non-Responses payload"
            )
        normalized = _normalize_responses_response(payload)
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
        if self.provider == "custom-model-provider":
            async for event in self._stream_openai_chat_completions(
                client, request, outbound
            ):
                yield event
            return
        async for event in self._stream_openai_responses(client, request, outbound):
            yield event

    async def _stream_openai_chat_completions(
        self,
        client: httpx.AsyncClient,
        request: NormalizedMessagesRequest,
        outbound: ProviderHTTPRequest,
    ) -> AsyncIterator[AnthropicStreamEvent]:
        """Wrapper around module-level streaming function."""
        async for event in _stream_openai_chat_completions(
            client, request, outbound, provider=self.provider
        ):
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
        text_had_deltas: set[str] = set()
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

                # OpenAI sets SSE event: field; some providers (OpenRouter)
                # only set the type in the JSON body
                event_name = sse_event.event or str(chunk.get("type", ""))
                response_payload = chunk.get("response")
                if isinstance(response_payload, dict):
                    usage = _responses_usage(response_payload)
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

                if event_name in (
                    "response.reasoning_text.delta",
                    "response.reasoning_summary_text.delta",
                ):
                    item_id = str(chunk.get("item_id", ""))
                    delta = chunk.get("delta")
                    if not (isinstance(delta, str) and delta):
                        allocate_content_index(
                            thinking_indices,
                            item_id,
                            chunk.get("output_index"),
                        )
                        continue
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
                                },
                            },
                        )
                        started_thinking_ids.add(item_id)
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
                        text_had_deltas.add(item_id)
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
                    if not had_started_thinking:
                        yield AnthropicStreamEvent(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": content_index,
                                "content_block": {
                                    "type": "thinking",
                                    "thinking": "",
                                },
                            },
                        )
                        started_thinking_ids.add(item_id)
                    # Extract reasoning text from the completed item if
                    # delta events were not sent (mirrors non-streaming
                    # _normalized_responses_content logic).
                    if not had_started_thinking:
                        thinking = "".join(
                            str(part.get("text", ""))
                            for part in item.get("content", [])
                            if isinstance(part, dict)
                            and part.get("type") == "reasoning_text"
                        )
                        if not thinking and isinstance(
                            summary := item.get("summary"), list
                        ):
                            thinking = "".join(
                                str(part.get("text", ""))
                                for part in summary
                                if isinstance(part, dict)
                            )
                        if thinking:
                            yield anthropic_thinking_delta_event(
                                content_index, thinking
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
                    if item_id not in text_had_deltas:
                        for part in item.get("content", []):
                            if (
                                isinstance(part, dict)
                                and part.get("type") == "output_text"
                                and isinstance(text := part.get("text"), str)
                                and text
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


__all__ = [
    "OpenAIFamilyAdapter",
]
