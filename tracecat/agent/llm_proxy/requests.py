"""Request and response normalization for the Tracecat LLM proxy."""

from __future__ import annotations

from typing import Any, Literal, TypedDict, cast
from uuid import UUID

import orjson

from tracecat.agent.llm_proxy.anthropic_compat import (
    OPENAI_MAX_TOOL_CALL_ID_LENGTH as _OPENAI_MAX_TOOL_CALL_ID_LENGTH,
)
from tracecat.agent.llm_proxy.anthropic_compat import (
    anthropic_tool_to_openai_tool,
    tool_choice_to_anthropic,
    tool_choice_to_openai,
    tool_definition_to_anthropic,
    tool_result_content_to_openai,
    tool_result_to_anthropic_block,
    truncate_tool_call_id,
)
from tracecat.agent.llm_proxy.types import (
    AnthropicStreamEvent,
    IngressFormat,
    NormalizedMessage,
    NormalizedMessagesRequest,
    NormalizedResponse,
    NormalizedToolCall,
)

OPENAI_MAX_TOOL_CALL_ID_LENGTH = _OPENAI_MAX_TOOL_CALL_ID_LENGTH
ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY = "_anthropic_content_blocks"

_DEFAULT_ALLOWED_MODEL_SETTING_KEYS = {
    "temperature",
    "max_tokens",
    "max_completion_tokens",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "stop",
    "reasoning_effort",
    "seed",
    "verbosity",
}

_PROVIDER_ALLOWED_MODEL_SETTING_KEYS: dict[str, frozenset[str]] = {
    "gemini": frozenset(
        {
            "top_k",
            "candidate_count",
            "response_mime_type",
            "response_schema",
            "response_json_schema",
        }
    ),
    "vertex_ai": frozenset(
        {
            "top_k",
            "candidate_count",
            "response_mime_type",
            "response_schema",
            "response_json_schema",
        }
    ),
    "bedrock": frozenset({"top_k", "thinking"}),
}


def _allowed_model_setting_keys(provider: str | None) -> set[str]:
    keys = set(_DEFAULT_ALLOWED_MODEL_SETTING_KEYS)
    if provider is not None:
        keys.update(_PROVIDER_ALLOWED_MODEL_SETTING_KEYS.get(provider, ()))
    return keys


_TOKEN_LIMIT_KEYS = {"max_tokens", "max_completion_tokens"}


def clamp_max_tokens(payload: dict[str, Any]) -> None:
    """Clamp max_tokens / max_completion_tokens to at least 1 in-place.

    The Claude Code CLI computes ``max_tokens = context_window - prompt_tokens``
    before sending the request.  When the conversation context exceeds the
    window the SDK may send a negative value which upstream providers reject.
    Clamping to 1 lets the provider return a normal "context too long" error
    instead of a confusing "max_tokens must be at least 1" 400.
    """
    for key in _TOKEN_LIMIT_KEYS:
        if (val := payload.get(key)) is not None and isinstance(val, (int, float)):
            if val < 1:
                payload[key] = 1


def filter_allowed_model_settings(
    model_settings: dict[str, Any],
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    """Keep only model settings supported by the selected provider family."""
    allowed_keys = _allowed_model_setting_keys(provider)
    filtered = {
        key: value for key, value in model_settings.items() if key in allowed_keys
    }
    clamp_max_tokens(filtered)
    return filtered


def _safe_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _parse_json_value(value: Any) -> Any:
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


def _metadata_with_anthropic_content_blocks(
    metadata: dict[str, Any],
    content: list[Any],
) -> dict[str, Any]:
    """Attach original Anthropic content blocks for exact provider replay."""
    return {
        **metadata,
        ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY: [
            dict(item) if isinstance(item, dict) else item for item in content
        ],
    }


def _normalized_tool_call_from_dict(data: dict[str, Any]) -> NormalizedToolCall:
    function = _safe_dict(data.get("function"))
    return NormalizedToolCall(
        id=str(data.get("id", "")),
        name=str(function.get("name", data.get("name", ""))),
        arguments=_parse_json_value(function.get("arguments", data.get("arguments"))),
    )


def _normalized_message_from_dict(data: dict[str, Any]) -> NormalizedMessage:
    tool_calls = tuple(
        _normalized_tool_call_from_dict(item)
        for item in data.get("tool_calls", [])
        if isinstance(item, dict)
    )
    role = _normalized_message_role(data.get("role", "user"))
    return NormalizedMessage(
        role=role,
        content=data.get("content"),
        tool_calls=tool_calls,
        tool_call_id=data.get("tool_call_id"),
        name=data.get("name"),
        metadata=_safe_dict(data.get("metadata")),
    )


def _normalized_message_role(
    value: Any,
) -> Literal["system", "user", "assistant", "tool"]:
    if value in {"system", "user", "assistant", "tool"}:
        return cast(Literal["system", "user", "assistant", "tool"], value)
    return "user"


def _system_messages_from_payload(
    payload: dict[str, Any],
) -> tuple[NormalizedMessage, ...]:
    system = payload.get("system")
    if system is None:
        return ()
    if isinstance(system, list):
        return tuple(NormalizedMessage(role="system", content=item) for item in system)
    return (NormalizedMessage(role="system", content=system),)


class _AnthropicRequestParts(TypedDict):
    messages: tuple[NormalizedMessage, ...]
    tools: tuple[dict[str, Any], ...]
    tool_choice: Any
    stream: bool
    model_settings: dict[str, Any]
    metadata: dict[str, Any]


def normalize_anthropic_request(
    payload: dict[str, Any],
    *,
    provider: str,
    model: str,
    base_url: str | None = None,
    use_workspace_credentials: bool = False,
    request_id: str | None = None,
    workspace_id: UUID | None = None,
    organization_id: UUID | None = None,
    session_id: UUID | None = None,
) -> NormalizedMessagesRequest:
    """Normalize an Anthropic Messages API request."""
    parts = extract_anthropic_request_parts(payload, provider=provider)
    return NormalizedMessagesRequest(
        provider=provider,
        model=str(payload.get("model", model)),
        messages=parts["messages"],
        output_format=IngressFormat.ANTHROPIC,
        stream=parts["stream"],
        base_url=base_url,
        use_workspace_credentials=use_workspace_credentials,
        tools=parts["tools"],
        tool_choice=parts["tool_choice"],
        model_settings=parts["model_settings"],
        request_id=request_id,
        workspace_id=workspace_id,
        organization_id=organization_id,
        session_id=session_id,
        metadata=parts["metadata"],
    )


def extract_anthropic_request_parts(
    payload: dict[str, Any],
    *,
    provider: str | None = None,
) -> _AnthropicRequestParts:
    """Extract the payload-owned Anthropic request fields.

    This parses user-controlled request content only. Callers that already have
    authoritative provider/model/workspace context should layer those values on
    separately instead of reparsing and then overriding them.
    """
    tool_name_by_id: dict[str, str] = {}
    normalized_messages: list[NormalizedMessage] = list(
        _system_messages_from_payload(payload)
    )
    for message in payload.get("messages", []):
        if not isinstance(message, dict):
            continue
        for normalized_message in _normalized_messages_from_anthropic_message(message):
            if normalized_message.role == "assistant":
                for tool_call in normalized_message.tool_calls:
                    tool_name_by_id[tool_call.id] = tool_call.name
            elif (
                normalized_message.role == "tool"
                and normalized_message.name is None
                and normalized_message.tool_call_id in tool_name_by_id
            ):
                normalized_message = NormalizedMessage(
                    role=normalized_message.role,
                    content=normalized_message.content,
                    tool_calls=normalized_message.tool_calls,
                    tool_call_id=normalized_message.tool_call_id,
                    name=tool_name_by_id[normalized_message.tool_call_id],
                    metadata=normalized_message.metadata,
                )
            normalized_messages.append(normalized_message)
    messages = tuple(normalized_messages)
    tools = tuple(tool for tool in payload.get("tools", []) if isinstance(tool, dict))
    model_settings = filter_allowed_model_settings(payload, provider=provider)
    return {
        "messages": messages,
        "tools": tools,
        "tool_choice": payload.get("tool_choice"),
        "stream": bool(payload.get("stream", False)),
        "model_settings": model_settings,
        "metadata": _safe_dict(payload.get("metadata")),
    }


def _message_to_openai(message: NormalizedMessage) -> dict[str, Any]:
    result: dict[str, Any] = {"role": message.role}
    if message.content is not None:
        if message.role == "tool":
            result["content"] = tool_result_content_to_openai(message.content)
        else:
            result["content"] = _content_to_openai(message.content)
    if message.name is not None:
        result["name"] = message.name
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": truncate_tool_call_id(tool_call.id),
                "type": tool_call.type,
                "function": {
                    "name": tool_call.name,
                    "arguments": _tool_arguments_to_openai(tool_call.arguments),
                },
            }
            for tool_call in message.tool_calls
        ]
    return result


def _message_to_anthropic(message: NormalizedMessage) -> dict[str, Any]:
    if message.role == "tool":
        return {
            "role": "user",
            "content": [
                tool_result_to_anthropic_block(
                    tool_use_id=message.tool_call_id,
                    content=message.content,
                    is_error=bool(message.metadata.get("is_error", False)),
                )
            ],
        }

    result: dict[str, Any] = {"role": message.role}
    content_blocks = _coerce_anthropic_content_blocks(message.content)
    if content_blocks:
        result["content"] = content_blocks
    elif message.content is not None:
        result["content"] = message.content
    if message.name is not None:
        result["name"] = message.name
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        result["content"] = content_blocks + [
            {
                "type": "tool_use",
                "id": tool_call.id,
                "name": tool_call.name,
                "input": tool_call.arguments,
            }
            for tool_call in message.tool_calls
        ]
    return result


def _tool_arguments_to_openai(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, bytes):
        return arguments.decode("utf-8", errors="ignore")
    try:
        return orjson.dumps(arguments).decode("utf-8")
    except TypeError:
        return str(arguments)


def _content_to_openai(content: Any) -> Any:
    if isinstance(content, dict):
        return [content]
    if not isinstance(content, list):
        return content

    return [
        item if isinstance(item, dict) else {"type": "text", "text": str(item)}
        for item in content
    ]


def _coerce_anthropic_content_blocks(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, dict):
                blocks.append({str(key): value for key, value in item.items()})
            else:
                blocks.append({"type": "text", "text": str(item)})
        return blocks
    if isinstance(content, dict):
        return [{str(key): value for key, value in content.items()}]
    return [{"type": "text", "text": str(content)}]


def _anthropic_system_payload(
    messages: tuple[NormalizedMessage, ...],
) -> str | list[dict[str, Any]] | None:
    """Render normalized system messages into Anthropic's accepted shape."""
    system_messages = [
        message.content
        for message in messages
        if message.role == "system" and message.content is not None
    ]
    if not system_messages:
        return None
    if all(isinstance(item, str) for item in system_messages):
        return "\n\n".join(system_messages)

    blocks: list[dict[str, Any]] = []
    for item in system_messages:
        blocks.extend(_coerce_anthropic_content_blocks(item))
    return blocks


def _normalized_messages_from_anthropic_message(
    data: dict[str, Any],
) -> tuple[NormalizedMessage, ...]:
    content = data.get("content")
    if not isinstance(content, list):
        return (_normalized_message_from_dict(data),)

    role = _normalized_message_role(data.get("role", "user"))
    metadata = _metadata_with_anthropic_content_blocks(
        _safe_dict(data.get("metadata")),
        content,
    )
    text_parts: list[Any] = []
    tool_calls: list[NormalizedToolCall] = []
    tool_results: list[NormalizedMessage] = []

    for item in content:
        if not isinstance(item, dict):
            text_parts.append(item)
            continue

        match item.get("type"):
            case "text":
                text_parts.append(item.get("text", ""))
            case "tool_use":
                tool_calls.append(
                    NormalizedToolCall(
                        id=str(item.get("id", "")),
                        name=str(item.get("name", "")),
                        arguments=_parse_json_value(item.get("input")),
                    )
                )
            case "tool_result":
                tool_results.append(
                    NormalizedMessage(
                        role="tool",
                        content=item.get("content"),
                        tool_call_id=str(item.get("tool_use_id", "")),
                        metadata={"is_error": bool(item.get("is_error", False))},
                    )
                )
            case _:
                text_parts.append(item)

    text_content: Any = None
    if text_parts:
        text_content = text_parts[0] if len(text_parts) == 1 else text_parts

    messages: list[NormalizedMessage] = []
    if role == "assistant":
        if tool_calls or text_content is not None:
            messages.append(
                NormalizedMessage(
                    role="assistant",
                    content=text_content,
                    tool_calls=tuple(tool_calls),
                    name=data.get("name"),
                    metadata=metadata,
                )
            )
    elif tool_results:
        messages.extend(tool_results)
        if text_content is not None:
            messages.append(
                NormalizedMessage(
                    role=role,
                    content=text_content,
                    name=data.get("name"),
                    metadata=metadata,
                )
            )
    else:
        messages.append(
            NormalizedMessage(
                role=role,
                content=text_content,
                name=data.get("name"),
                metadata=metadata,
            )
        )
    return tuple(messages)


def messages_request_to_openai_payload(
    request: NormalizedMessagesRequest,
) -> dict[str, Any]:
    """Render a provider messages request into an OpenAI chat payload."""
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": [_message_to_openai(message) for message in request.messages],
    }
    if request.tools:
        payload["tools"] = [
            anthropic_tool_to_openai_tool(dict(tool)) for tool in request.tools
        ]
    if request.tool_choice is not None and request.tools:
        payload["tool_choice"] = tool_choice_to_openai(request.tool_choice)
    if request.parallel_tool_calls is not None and request.tools:
        payload["parallel_tool_calls"] = request.parallel_tool_calls
    if request.response_format is not None:
        payload["response_format"] = request.response_format
    payload.update(request.model_settings)
    payload["stream"] = request.stream
    return payload


def messages_request_to_anthropic_payload(
    request: NormalizedMessagesRequest,
) -> dict[str, Any]:
    """Render a provider messages request into an Anthropic payload."""
    messages = [
        _message_to_anthropic(message)
        for message in request.messages
        if message.role != "system"
    ]
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": messages,
        "stream": request.stream,
    }
    if system_payload := _anthropic_system_payload(request.messages):
        payload["system"] = system_payload
    if request.tools:
        payload["tools"] = [
            tool_definition_to_anthropic(dict(tool)) for tool in request.tools
        ]
    if request.tool_choice is not None:
        payload["tool_choice"] = tool_choice_to_anthropic(request.tool_choice)
    payload.update(request.model_settings)
    return payload


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


def normalize_openai_response(payload: dict[str, Any]) -> NormalizedResponse:
    """Normalize an OpenAI chat-completions payload."""
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    tool_calls = tuple(
        _normalized_tool_call_from_dict(item)
        for item in message.get("tool_calls", [])
        if isinstance(item, dict)
    )
    return NormalizedResponse(
        provider="openai",
        model=str(payload.get("model", "")),
        content=message.get("content"),
        tool_calls=tool_calls,
        finish_reason=choice.get("finish_reason"),
        usage=_safe_dict(payload.get("usage")),
        raw=_safe_dict(payload),
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
                        arguments=_safe_dict(item.get("input")),
                    )
                )
            elif item_type == "text":
                text_parts.append(item.get("text", ""))
            else:
                text_parts.append(item)
    elif content is not None:
        text_parts.append(content)
    return NormalizedResponse(
        provider="anthropic",
        model=str(payload.get("model", "")),
        content=text_parts if len(text_parts) != 1 else text_parts[0],
        tool_calls=tuple(tool_calls),
        finish_reason=payload.get("stop_reason"),
        usage={
            "input_tokens": int(payload.get("usage", {}).get("input_tokens", 0)),
            "output_tokens": int(payload.get("usage", {}).get("output_tokens", 0)),
        },
        raw=_safe_dict(payload),
    )
