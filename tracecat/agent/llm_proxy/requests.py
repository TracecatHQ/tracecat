"""Request normalization and payload rendering for the Tracecat LLM proxy."""

from __future__ import annotations

from typing import Any, Literal, TypedDict, cast
from uuid import UUID

import orjson

from tracecat.agent.llm_proxy.content_blocks import (
    _SERVER_TOOL_RESULT_TYPES,
    _SERVER_TOOL_USE_TYPES,
    coerce_anthropic_content_blocks,
    format_server_tool_result_content,
    metadata_with_anthropic_content_blocks,
)
from tracecat.agent.llm_proxy.tool_compat import (
    OPENAI_MAX_TOOL_CALL_ID_LENGTH as _OPENAI_MAX_TOOL_CALL_ID_LENGTH,
)
from tracecat.agent.llm_proxy.tool_compat import (
    anthropic_tool_to_openai_tool,
    tool_choice_to_anthropic,
    tool_choice_to_openai,
    tool_definition_to_anthropic,
    tool_result_content_to_openai,
    tool_result_to_anthropic_block,
    truncate_tool_call_id,
)
from tracecat.agent.llm_proxy.types import (
    IngressFormat,
    NormalizedMessage,
    NormalizedMessagesRequest,
    NormalizedToolCall,
)

OPENAI_MAX_TOOL_CALL_ID_LENGTH = _OPENAI_MAX_TOOL_CALL_ID_LENGTH

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


# ---------------------------------------------------------------------------
# Normalized message construction
# ---------------------------------------------------------------------------


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


def _normalized_messages_from_anthropic_message(
    data: dict[str, Any],
) -> tuple[NormalizedMessage, ...]:
    content = data.get("content")
    if not isinstance(content, list):
        return (_normalized_message_from_dict(data),)

    role = _normalized_message_role(data.get("role", "user"))
    metadata = metadata_with_anthropic_content_blocks(
        _safe_dict(data.get("metadata")),
        content,
    )
    content_parts: list[Any] = []
    tool_calls: list[NormalizedToolCall] = []
    tool_results: list[NormalizedMessage] = []

    for item in content:
        if not isinstance(item, dict):
            content_parts.append(item)
            continue

        match item.get("type"):
            case "text":
                content_parts.append(item.get("text", ""))
            case "thinking":
                content_parts.append({str(key): value for key, value in item.items()})
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
            case "tool_reference":
                # Content-free pointer to a tool name — drop.
                continue
            case block_type if block_type in _SERVER_TOOL_USE_TYPES:
                # server_tool_use, mcp_tool_use, container_tool_use
                # → normalise as a regular tool call.
                tool_calls.append(
                    NormalizedToolCall(
                        id=str(item.get("id", "")),
                        name=str(item.get("name", "")),
                        arguments=_parse_json_value(item.get("input")),
                    )
                )
            case block_type if block_type in _SERVER_TOOL_RESULT_TYPES:
                # web_search_tool_result, code_execution_tool_result,
                # mcp_tool_result, container_tool_result
                # → normalise as a regular tool result.
                result_content = format_server_tool_result_content(
                    block_type, item.get("content")
                )
                tool_results.append(
                    NormalizedMessage(
                        role="tool",
                        content=result_content,
                        tool_call_id=str(item.get("tool_use_id", "")),
                        metadata={
                            "is_error": bool(item.get("is_error", False)),
                        },
                    )
                )
            case _:
                content_parts.append(item)

    text_content: Any = None
    if content_parts:
        text_content = content_parts[0] if len(content_parts) == 1 else content_parts

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


# ---------------------------------------------------------------------------
# Request rendering (normalized → provider payload)
# ---------------------------------------------------------------------------


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
    content_blocks = coerce_anthropic_content_blocks(message.content)
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
        blocks.extend(coerce_anthropic_content_blocks(item))
    return blocks


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
