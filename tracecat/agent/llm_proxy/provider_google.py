"""Google-family upstream adapters for the Tracecat LLM proxy.

This module keeps Gemini and Vertex shaping local and explicit.
The shared request model already carries enough information for the
most important hardening gaps:

- system instruction extraction
- tool declaration shaping
- tool choice shaping
- response format shaping
- conservative content normalization
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from mimetypes import guess_type
from typing import Any, TypedDict

import httpx
import orjson

from tracecat.agent.common.output_format import extract_json_schema
from tracecat.agent.llm_proxy.content_blocks import (
    ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY,
)
from tracecat.agent.llm_proxy.tool_compat import (
    anthropic_tool_choice,
    anthropic_tool_definition,
)
from tracecat.agent.llm_proxy.types import (
    NormalizedMessage,
    NormalizedMessagesRequest,
    NormalizedResponse,
    NormalizedToolCall,
    ProviderHTTPRequest,
)

_DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
_DEFAULT_VERTEX_LOCATION = "us-central1"


class _GoogleSystemInstruction(TypedDict):
    """Top-level system instruction payload for Gemini-style APIs."""

    parts: list[dict[str, Any]]


class _GoogleContentMessage(TypedDict):
    """One message entry in the Google `contents` array."""

    role: str
    parts: list[dict[str, Any]]


class _GooglePayloadParts(TypedDict):
    """Structured request fragments before Google payload assembly."""

    contents: list[_GoogleContentMessage]
    generation_config: dict[str, Any]
    tools: list[dict[str, Any]]
    tool_config: dict[str, Any] | None
    system_instruction: _GoogleSystemInstruction | None


class _GooglePayload(TypedDict, total=False):
    """Top-level Gemini/Vertex request payload."""

    contents: list[_GoogleContentMessage]
    generationConfig: dict[str, Any]
    tools: list[dict[str, Any]]
    toolConfig: dict[str, Any]
    # Gemini AI Studio uses `systemInstruction`; Vertex AI uses
    # `system_instruction`. We keep both keys in the shared payload type
    # because `_normalize_google_payload()` emits exactly one based on the
    # target API.
    systemInstruction: _GoogleSystemInstruction
    system_instruction: _GoogleSystemInstruction


def _vertex_base_url(location: str) -> str:
    normalized_location = location.strip().lower() or _DEFAULT_VERTEX_LOCATION
    if normalized_location == "global":
        return "https://aiplatform.googleapis.com"
    return f"https://{normalized_location}-aiplatform.googleapis.com"


def _strip_model_prefix(model: str, prefix: str) -> str:
    if model.startswith(prefix):
        return model.removeprefix(prefix)
    return model


def _text_part(text: Any) -> dict[str, Any]:
    return {"text": str(text)}


def _guess_mime_type(value: str, fallback: str = "application/octet-stream") -> str:
    if value.startswith("data:"):
        header = value[5:].split(",", 1)[0]
        mime_type = header.split(";", 1)[0].strip()
        if mime_type:
            return mime_type
    if guessed := guess_type(value)[0]:
        return guessed
    return fallback


def _media_part_from_reference(
    reference: str, format_name: str | None = None
) -> dict[str, Any]:
    mime_type = format_name or _guess_mime_type(reference)
    if reference.startswith("http://") or reference.startswith("https://"):
        return {"fileData": {"mimeType": mime_type, "fileUri": reference}}
    if reference.startswith("gs://"):
        return {"fileData": {"mimeType": mime_type, "fileUri": reference}}
    if reference.startswith("data:"):
        header, data = reference[5:].split(",", 1)
        inline_mime_type = header.split(";", 1)[0] or mime_type
        return {"inlineData": {"mimeType": inline_mime_type, "data": data}}
    return {"text": reference}


def _tool_arguments(arguments: Any) -> Any:
    if isinstance(arguments, (dict, list, int, float, bool)) or arguments is None:
        return arguments if arguments is not None else {}
    if isinstance(arguments, str):
        try:
            parsed = orjson.loads(arguments)
        except orjson.JSONDecodeError:
            return arguments
        if isinstance(parsed, (dict, list)):
            return parsed
        return {"value": parsed}
    return arguments


def _google_thought_signature(part: dict[str, Any]) -> str:
    signature = part.get("thoughtSignature")
    if signature is None:
        signature = part.get("signature")
    if signature is None:
        return ""
    return str(signature)


def _google_thought_part(
    text: Any,
    *,
    signature: str = "",
) -> dict[str, Any]:
    part: dict[str, Any] = {"text": str(text), "thought": True}
    if signature:
        part["thoughtSignature"] = signature
    return part


def _google_function_call_part(
    name: str,
    arguments: Any,
    *,
    thought_signature: str = "",
) -> dict[str, Any]:
    part: dict[str, Any] = {
        "functionCall": {
            "name": name,
            "args": _tool_arguments(arguments),
        }
    }
    if thought_signature:
        part["thoughtSignature"] = thought_signature
    return part


def _content_item_to_part(item: Any) -> dict[str, Any]:
    match item:
        case {"type": "text", "text": str(text)}:
            return _text_part(text)
        case {"type": "tool_use", "name": str(name), "input": arguments, **_rest}:
            thought_signature = ""
            if isinstance(_rest.get("thoughtSignature"), str):
                thought_signature = _rest["thoughtSignature"]
            elif isinstance(_rest.get("signature"), str):
                thought_signature = _rest["signature"]
            return _google_function_call_part(
                name,
                arguments,
                thought_signature=thought_signature,
            )
        case {"type": "thinking", **rest}:
            text = str(rest["thinking"]) if rest.get("thinking") else ""
            signature = ""
            if isinstance(rest.get("signature"), str):
                signature = rest["signature"]
            elif isinstance(rest.get("thoughtSignature"), str):
                signature = rest["thoughtSignature"]
            return _google_thought_part(text, signature=signature)
        case {"type": "image_url", "image_url": {"url": str(url), **image_info}}:
            return _media_part_from_reference(
                url,
                format_name=str(image_info.get("format"))
                if image_info.get("format") is not None
                else None,
            )
        case {"type": "image_url", "image_url": str(url)}:
            return _media_part_from_reference(url)
        case {"type": "file", "file": {"file_id": str(file_id), **file_info}}:
            return _media_part_from_reference(
                file_id,
                format_name=str(file_info.get("format"))
                if file_info.get("format") is not None
                else None,
            )
        case {
            "type": "input_audio",
            "input_audio": {"data": str(data), "format": str(fmt)},
        }:
            return {"inlineData": {"mimeType": fmt, "data": data}}
        case {"type": "function_call", "function_call": dict(function_call)}:
            return {
                "functionCall": {
                    "name": str(function_call.get("name", "")),
                    "args": _tool_arguments(function_call.get("arguments")),
                }
            }
        case _ if isinstance(item, dict):
            return dict(item)
        case _:
            return _text_part(item)


def _anthropic_blocks_to_google_parts(
    blocks: Sequence[Any],
) -> list[dict[str, Any]]:
    """Translate Anthropic-style content blocks into Google `parts`.

    Google can associate a thought signature with the following function call,
    while Anthropic models emit the thought and tool-use as separate blocks.
    We preserve that relationship by carrying the signature forward when a
    thinking block is immediately followed by a tool-use block.
    """

    parts: list[dict[str, Any]] = []
    pending_thought_signature = ""
    index = 0
    while index < len(blocks):
        item = blocks[index]
        next_item = blocks[index + 1] if index + 1 < len(blocks) else None
        if not isinstance(item, dict):
            parts.append(_text_part(item))
            index += 1
            continue

        match item.get("type"):
            case "thinking":
                text = str(item.get("thinking", ""))
                signature = _google_thought_signature(item)
                next_is_tool_use = (
                    isinstance(next_item, dict) and next_item.get("type") == "tool_use"
                )
                if next_is_tool_use:
                    # Gemini attaches the signature to the function call part
                    # rather than to a standalone thought block, so we keep the
                    # visible thought text but defer the signature one step.
                    if text:
                        parts.append(_google_thought_part(text))
                    pending_thought_signature = signature
                else:
                    parts.append(_google_thought_part(text, signature=signature))
                    pending_thought_signature = ""
            case "tool_use":
                tool_use_part = _content_item_to_part(item)
                if pending_thought_signature:
                    tool_use_part["thoughtSignature"] = pending_thought_signature
                    pending_thought_signature = ""
                parts.append(tool_use_part)
            case _:
                parts.append(_content_item_to_part(item))
                pending_thought_signature = ""
        index += 1

    return parts


def _anthropic_content_blocks_from_message(
    message: NormalizedMessage,
) -> list[Any] | None:
    blocks = message.metadata.get(ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY)
    if isinstance(blocks, list):
        return blocks
    return None


def _message_blocks_for_google(message: NormalizedMessage) -> list[Any] | None:
    """Return Anthropic-style blocks when Google replay should preserve order."""
    if blocks := _anthropic_content_blocks_from_message(message):
        return blocks
    if isinstance(message.content, list) and any(
        isinstance(item, dict) and item.get("type") in {"thinking", "tool_use"}
        for item in message.content
    ):
        return message.content
    return None


def _message_to_parts(message: NormalizedMessage) -> list[dict[str, Any]]:
    """Convert one normalized message into Google `parts`.

    Tool messages become `functionResponse` parts. Assistant thinking/tool-use
    history is replayed from Anthropic-style blocks when available so Gemini
    preserves the original ordering between thoughts and function calls.
    """

    if message.role == "tool":
        response: dict[str, Any] = {}
        if isinstance(message.content, dict):
            response = dict(message.content)
        elif isinstance(message.content, list):
            text_parts = [
                str(item["text"])
                for item in message.content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and item.get("text") is not None
            ]
            response = {
                "content": "\n".join(text_parts)
                if text_parts
                else [
                    dict(item) if isinstance(item, dict) else str(item)
                    for item in message.content
                ]
            }
        elif message.content is not None:
            response = {"content": str(message.content)}

        return [
            {
                "functionResponse": {
                    "name": message.name or "",
                    "response": response,
                }
            }
        ]

    if anthropic_blocks := _message_blocks_for_google(message):
        return _anthropic_blocks_to_google_parts(anthropic_blocks)

    parts: list[dict[str, Any]] = []
    if isinstance(message.content, list):
        parts.extend(_content_item_to_part(item) for item in message.content)
    elif message.content is not None:
        if isinstance(message.content, dict):
            parts.append(_content_item_to_part(message.content))
        else:
            parts.append(_text_part(message.content))

    for tool_call in message.tool_calls:
        parts.append(
            {
                "functionCall": {
                    "name": tool_call.name,
                    "args": _tool_arguments(tool_call.arguments),
                }
            }
        )
    return parts


def _extract_text_from_content(content: Any) -> str:
    """Extract plain text from any content shape (str, block list, or dict)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            match item:
                case {"type": "text", "text": str(text)}:
                    parts.append(text)
                case str(text):
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(content, dict):
        match content:
            case {"type": "text", "text": str(text)}:
                return text
    return str(content)


def _system_instruction_from_messages(
    messages: Sequence[NormalizedMessage],
) -> _GoogleSystemInstruction | None:
    system_texts = [
        _extract_text_from_content(message.content)
        for message in messages
        if message.role == "system" and message.content is not None
    ]
    if not system_texts:
        return None
    return {"parts": [{"text": "\n\n".join(system_texts)}]}


def _content_messages_from_request(
    messages: Sequence[NormalizedMessage],
    *,
    system_instruction_key: str,
) -> list[_GoogleContentMessage]:
    """Build Google `contents`, moving system prompts to top-level config.

    Gemini and Vertex reject requests without any content messages, so we emit
    a single blank user turn when the normalized transcript would otherwise be
    empty.
    """

    del system_instruction_key
    contents: list[_GoogleContentMessage] = []
    for message in messages:
        if message.role == "system":
            continue
        parts = _message_to_parts(message)
        if not parts:
            continue
        contents.append(
            {
                "role": "model" if message.role == "assistant" else "user",
                "parts": parts,
            }
        )

    if contents:
        return contents
    return [{"role": "user", "parts": [{"text": " "}]}]


# Keys that Gemini's OpenAPI-subset schema actually accepts.
# Everything else gets stripped recursively.
_GEMINI_ALLOWED_SCHEMA_KEYS = frozenset(
    {
        "type",
        "format",
        "description",
        "nullable",
        "enum",
        "items",
        "properties",
        "required",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "anyOf",
        "oneOf",
        "title",
    }
)


def _resolve_local_schema_refs(
    schema: Any,
    defs: dict[str, Any] | None = None,
    *,
    seen: frozenset[str] = frozenset(),
) -> Any:
    """Inline local ``#/$defs/...`` references before Gemini schema filtering."""
    if isinstance(schema, list):
        return [_resolve_local_schema_refs(item, defs, seen=seen) for item in schema]
    if not isinstance(schema, dict):
        return schema

    local_defs = defs or {}
    if isinstance(schema.get("$defs"), dict):
        local_defs = {**local_defs, **schema["$defs"]}

    if isinstance(ref := schema.get("$ref"), str) and ref.startswith("#/$defs/"):
        def_name = ref.removeprefix("#/$defs/")
        if def_name in seen:
            return {}
        if isinstance(target_schema := local_defs.get(def_name), dict):
            resolved_target = _resolve_local_schema_refs(
                target_schema,
                local_defs,
                seen=seen | {def_name},
            )
            if not isinstance(resolved_target, dict):
                return resolved_target
            local_overrides = {
                key: _resolve_local_schema_refs(value, local_defs, seen=seen)
                for key, value in schema.items()
                if key not in {"$defs", "$ref"}
            }
            return {
                **resolved_target,
                **local_overrides,
            }
        return {}

    return {
        key: _resolve_local_schema_refs(value, local_defs, seen=seen)
        for key, value in schema.items()
        if key != "$defs"
    }


def _sanitize_schema_for_gemini(schema: Any) -> Any:
    """Recursively keep only keys Gemini accepts in function parameter schemas."""
    if not isinstance(schema, dict):
        return schema
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            properties = {
                name: sanitized
                for name, property_schema in value.items()
                if isinstance(property_schema, dict)
                and (sanitized := _sanitize_schema_for_gemini(property_schema))
            }
            if properties:
                cleaned[key] = properties
            continue
        if key == "items":
            if isinstance(value, dict):
                cleaned[key] = _sanitize_schema_for_gemini(value) or {"type": "object"}
            elif isinstance(value, list):
                sanitized_items = [
                    sanitized
                    for item in value
                    if isinstance(item, dict)
                    and (sanitized := _sanitize_schema_for_gemini(item))
                ]
                cleaned[key] = (
                    sanitized_items[0] if sanitized_items else {"type": "object"}
                )
            continue
        if key in {"anyOf", "oneOf"} and isinstance(value, list):
            if sanitized_variants := [
                sanitized
                for item in value
                if isinstance(item, dict)
                and (sanitized := _sanitize_schema_for_gemini(item))
            ]:
                cleaned[key] = sanitized_variants
            continue
        if key not in _GEMINI_ALLOWED_SCHEMA_KEYS:
            continue
        if isinstance(value, dict):
            cleaned[key] = _sanitize_schema_for_gemini(value)
        elif isinstance(value, list):
            cleaned[key] = [_sanitize_schema_for_gemini(item) for item in value]
        else:
            cleaned[key] = value
    if isinstance(properties := cleaned.get("properties"), dict):
        if isinstance(required := cleaned.get("required"), list):
            filtered_required = [
                name
                for name in required
                if isinstance(name, str) and name in properties
            ]
            if filtered_required:
                cleaned["required"] = filtered_required
            else:
                cleaned.pop("required", None)
    else:
        cleaned.pop("required", None)
    if cleaned.get("type") == "array" and "items" not in cleaned:
        cleaned["items"] = {"type": "object"}
    return cleaned


def _prepare_schema_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    return _sanitize_schema_for_gemini(_resolve_local_schema_refs(schema))


def _tools_from_request(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    google_tools: list[dict[str, Any]] = []
    for tool in tools:
        if definition := anthropic_tool_definition(tool):
            declaration: dict[str, Any] = {"name": definition["name"]}
            if description := definition.get("description"):
                declaration["description"] = description
            declaration["parameters"] = _prepare_schema_for_gemini(
                definition["input_schema"]
            )
            google_tools.append({"functionDeclarations": [declaration]})
    return google_tools


def _tool_choice_to_config(tool_choice: Any) -> dict[str, Any] | None:
    if (anthropic_choice := anthropic_tool_choice(tool_choice)) is None:
        return None
    match anthropic_choice["type"]:
        case "tool":
            return {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": [anthropic_choice["name"]],
                }
            }
        case "any":
            return {"functionCallingConfig": {"mode": "ANY"}}
        case "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        case "none":
            return {"functionCallingConfig": {"mode": "NONE"}}
        case _:
            return None


def _response_format_to_generation_config(
    response_format: dict[str, Any] | None,
) -> dict[str, Any]:
    if not response_format:
        return {}

    response_type = str(response_format.get("type", ""))
    if response_type == "text":
        return {}

    generation_config: dict[str, Any] = {"responseMimeType": "application/json"}
    if response_schema := extract_json_schema(response_format):
        generation_config["responseSchema"] = _prepare_schema_for_gemini(
            response_schema
        )
    return generation_config


def _is_gemini_3_model(model: str) -> bool:
    """Return True for Gemini 3.x models (which use thinkingLevel)."""
    name = model.lower().split("/")[-1]
    return "gemini-3" in name


def _thinking_config_from_reasoning_effort(
    reasoning_effort: str,
    *,
    model: str,
) -> dict[str, Any] | None:
    """Build thinkingConfig for Gemini models.

    Gemini 3.x uses thinkingLevel; Gemini 2.5 uses thinkingBudget.
    The two are mutually exclusive.
    """
    level = reasoning_effort.lower()
    if level not in {"minimal", "low", "medium", "high"}:
        return None
    if _is_gemini_3_model(model):
        return {"includeThoughts": True, "thinkingLevel": level.upper()}
    return {
        "includeThoughts": True,
        "thinkingBudget": {"minimal": 1024, "low": 1024, "medium": 2048, "high": 4096}[
            level
        ],
    }


def _generation_config_from_request(
    request: NormalizedMessagesRequest,
) -> dict[str, Any]:
    """Map normalized model settings onto Google `generationConfig`.

    This keeps generic knobs in one place and lets explicit caller-provided
    schema overrides win after the normalized defaults are derived.
    """

    config: dict[str, Any] = {}
    settings = dict(request.model_settings)

    for source_key, target_key in (
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("top_k", "top_k"),
        ("candidate_count", "candidate_count"),
        ("max_completion_tokens", "max_output_tokens"),
        ("max_tokens", "max_output_tokens"),
        ("stop", "stop_sequences"),
    ):
        if (value := settings.get(source_key)) is None:
            continue
        if source_key == "stop" and isinstance(value, str):
            config[target_key] = [value]
            continue
        config[target_key] = value

    if reasoning_effort := settings.get("reasoning_effort"):
        if thinking_config := _thinking_config_from_reasoning_effort(
            str(reasoning_effort),
            model=request.model,
        ):
            config["thinkingConfig"] = thinking_config

    if response_format_config := _response_format_to_generation_config(
        request.response_format
    ):
        config.update(response_format_config)

    # Preserve already-normalized overrides when callers send them directly.
    for key in ("response_mime_type", "response_schema", "response_json_schema"):
        if value := settings.get(key):
            if key == "response_json_schema" and isinstance(value, dict):
                config["responseSchema"] = _prepare_schema_for_gemini(
                    extract_json_schema(value) or value
                )
                continue
            if key == "response_mime_type":
                config["responseMimeType"] = value
                continue
            if key == "response_schema":
                config["responseSchema"] = _prepare_schema_for_gemini(value)

    return config


def _normalize_google_payload(
    request: NormalizedMessagesRequest,
    *,
    system_instruction_key: str,
) -> dict[str, Any]:
    """Assemble one Google-family payload for Gemini or Vertex.

    The only structural difference we care about here is the top-level system
    instruction key name; the rest of the payload shape is shared.
    """

    tools = _tools_from_request(request.tools) if request.tools else []
    payload_parts: _GooglePayloadParts = {
        "contents": _content_messages_from_request(
            request.messages,
            system_instruction_key=system_instruction_key,
        ),
        "system_instruction": _system_instruction_from_messages(request.messages),
        "generation_config": _generation_config_from_request(request),
        "tools": tools,
        "tool_config": _tool_choice_to_config(request.tool_choice),
    }
    payload: _GooglePayload = {"contents": payload_parts["contents"]}
    if payload_parts["system_instruction"] is not None:
        match system_instruction_key:
            case "systemInstruction":
                payload["systemInstruction"] = payload_parts["system_instruction"]
            case "system_instruction":
                payload["system_instruction"] = payload_parts["system_instruction"]
            case _:
                raise ValueError(
                    f"Unsupported Google system instruction key: {system_instruction_key}"
                )
    if payload_parts["generation_config"]:
        payload["generationConfig"] = payload_parts["generation_config"]
    if payload_parts["tools"]:
        payload["tools"] = payload_parts["tools"]
    if payload_parts["tool_config"] is not None:
        payload["toolConfig"] = payload_parts["tool_config"]
    return dict(payload)


def _parse_google_response(
    response: httpx.Response,
    *,
    provider: str,
) -> NormalizedResponse:
    """Normalize Google responses into Tracecat content and tool calls.

    Google may interleave visible text, hidden thoughts, and function calls in
    the same `parts` array. We preserve early plain text as a simple string
    until reasoning appears; once a thought is seen, the remainder is promoted
    into Anthropic-style content blocks so ordering is not lost.
    """

    if response.status_code >= 400:
        try:
            error_body = response.json()
        except Exception:
            error_body = response.text[:1000]
        raise httpx.HTTPStatusError(
            f"Google API error {response.status_code}: {error_body}",
            request=response.request,
            response=response,
        )

    payload = response.json()
    candidates = payload.get("candidates") or [{}]
    candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
    content = candidate.get("content") or {}
    parts = content.get("parts") or []

    text_parts: list[str] = []
    content_blocks: list[dict[str, Any]] = []
    tool_calls: list[NormalizedToolCall] = []
    reasoning_seen = False

    def _promote_text_parts() -> None:
        nonlocal text_parts
        if not text_parts:
            return
        content_blocks.extend({"type": "text", "text": text} for text in text_parts)
        text_parts = []

    def _append_reasoning_block(
        text: str,
        *,
        signature: str = "",
    ) -> None:
        nonlocal reasoning_seen
        reasoning_seen = True
        content_blocks.append(
            {
                "type": "thinking",
                "thinking": text,
                "signature": signature,
            }
        )

    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            if reasoning_seen:
                content_blocks.append({"type": "text", "text": str(part)})
            else:
                text_parts.append(str(part))
            continue

        if part.get("thought") is True and "text" in part:
            _promote_text_parts()
            _append_reasoning_block(
                str(part.get("text", "")),
                signature=_google_thought_signature(part),
            )
            continue

        function_call = part.get("functionCall") or part.get("function_call")
        if isinstance(function_call, dict):
            thought_signature = _google_thought_signature(part)
            if thought_signature:
                _promote_text_parts()
                # Some Google responses attach a thought signature to the
                # function-call part instead of emitting a separate thought
                # block. Reattach that signature to the most recent thinking
                # block when possible; otherwise synthesize an empty one so the
                # reasoning state is still represented in-order.
                if (
                    content_blocks
                    and content_blocks[-1].get("type") == "thinking"
                    and not content_blocks[-1].get("signature")
                ):
                    content_blocks[-1]["signature"] = thought_signature
                else:
                    _append_reasoning_block("", signature=thought_signature)
            name = str(function_call.get("name", ""))
            arguments = _tool_arguments(
                function_call.get("args") or function_call.get("arguments")
            )
            tool_calls.append(
                NormalizedToolCall(
                    id=f"{name or 'tool'}-{index}",
                    name=name,
                    arguments=arguments,
                )
            )
            continue

        if text := part.get("text"):
            if part.get("thought") is True:
                _promote_text_parts()
                _append_reasoning_block(
                    str(text),
                    signature=_google_thought_signature(part),
                )
            elif reasoning_seen:
                content_blocks.append({"type": "text", "text": str(text)})
            else:
                text_parts.append(str(text))
            continue

        if reasoning_seen:
            content_blocks.append(dict(part))
        else:
            text_parts.append(str(part))

    usage_metadata = payload.get("usageMetadata") or {}
    if reasoning_seen:
        _promote_text_parts()
        normalized_content: Any = content_blocks
    elif len(text_parts) == 1:
        normalized_content = text_parts[0]
    elif text_parts:
        normalized_content = text_parts
    else:
        normalized_content = None
    return NormalizedResponse(
        provider=provider,
        model=str(payload.get("model", "")),
        content=normalized_content,
        tool_calls=tuple(tool_calls),
        finish_reason=str(candidate.get("finishReason"))
        if candidate.get("finishReason")
        else None,
        usage={
            "input_tokens": int(usage_metadata.get("promptTokenCount", 0)),
            "output_tokens": int(usage_metadata.get("candidatesTokenCount", 0)),
        },
        raw=payload,
    )


@dataclass(slots=True)
class GeminiAdapter:
    """Adapter for Google AI Studio Gemini models."""

    provider: str = "gemini"

    def prepare_request(
        self, request: NormalizedMessagesRequest, credentials: dict[str, str]
    ) -> ProviderHTTPRequest:
        api_key = credentials.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Gemini requires GEMINI_API_KEY")

        base_url = (request.base_url or _DEFAULT_GEMINI_BASE_URL).rstrip("/")
        model = _strip_model_prefix(request.model, "gemini/")
        payload = _normalize_google_payload(
            request,
            system_instruction_key="systemInstruction",
        )
        return ProviderHTTPRequest(
            method="POST",
            url=f"{base_url}/v1beta/models/{model}:generateContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            json_body=payload,
            stream=request.stream,
        )

    async def parse_response(
        self, response: httpx.Response, request: NormalizedMessagesRequest
    ) -> NormalizedResponse:
        del request
        return _parse_google_response(response, provider=self.provider)


@dataclass(slots=True)
class VertexAIAdapter:
    """Adapter for Google Vertex AI Gemini models."""

    provider: str = "vertex_ai"

    def prepare_request(
        self, request: NormalizedMessagesRequest, credentials: dict[str, str]
    ) -> ProviderHTTPRequest:
        project = credentials.get("GOOGLE_CLOUD_PROJECT")
        bearer_token = credentials.get("VERTEX_AI_BEARER_TOKEN")
        if not project or not bearer_token:
            raise ValueError(
                "Vertex AI requires GOOGLE_CLOUD_PROJECT and a resolved bearer token "
                "(VERTEX_AI_BEARER_TOKEN). Ensure credentials are resolved through "
                "the credential layer."
            )

        location = (
            (credentials.get("GOOGLE_CLOUD_LOCATION") or _DEFAULT_VERTEX_LOCATION)
            .strip()
            .lower()
        )
        base_url = (
            request.base_url.rstrip("/")
            if request.base_url
            else _vertex_base_url(location)
        )
        model = credentials.get("VERTEX_AI_MODEL", request.model)

        payload = _normalize_google_payload(
            request,
            system_instruction_key="system_instruction",
        )
        return ProviderHTTPRequest(
            method="POST",
            url=(
                f"{base_url}/v1/projects/{project}/locations/{location}"
                f"/publishers/google/models/{model}:generateContent"
            ),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {bearer_token}",
            },
            json_body=payload,
            stream=request.stream,
        )

    async def parse_response(
        self, response: httpx.Response, request: NormalizedMessagesRequest
    ) -> NormalizedResponse:
        del request
        return _parse_google_response(response, provider=self.provider)


__all__ = [
    "GeminiAdapter",
    "VertexAIAdapter",
]
