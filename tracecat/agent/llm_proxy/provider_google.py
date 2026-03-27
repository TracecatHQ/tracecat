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
from typing import Any

import httpx
import orjson

from tracecat.agent.common.output_format import extract_json_schema
from tracecat.agent.llm_proxy.anthropic_compat import (
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


def _content_item_to_part(item: Any) -> dict[str, Any]:
    match item:
        case {"type": "text", "text": str(text)}:
            return _text_part(text)
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


def _message_to_parts(message: NormalizedMessage) -> list[dict[str, Any]]:
    if message.tool_calls:
        parts: list[dict[str, Any]] = []
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

    if isinstance(message.content, list):
        return [_content_item_to_part(item) for item in message.content]
    if message.content is None:
        return []
    if isinstance(message.content, dict):
        return [_content_item_to_part(message.content)]
    return [_text_part(message.content)]


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
) -> dict[str, Any] | None:
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
) -> list[dict[str, Any]]:
    del system_instruction_key
    contents: list[dict[str, Any]] = []
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


def _thinking_config_from_reasoning_effort(
    reasoning_effort: str,
) -> dict[str, Any] | None:
    level = reasoning_effort.lower()
    if level not in {"minimal", "low", "medium", "high"}:
        return None
    budget_map = {
        "minimal": 1024,
        "low": 1024,
        "medium": 2048,
        "high": 4096,
    }
    return {
        "includeThoughts": True,
        "thinkingBudget": budget_map[level],
        "thinkingLevel": level,
    }


def _generation_config_from_request(
    request: NormalizedMessagesRequest,
) -> dict[str, Any]:
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
            str(reasoning_effort)
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
    payload: dict[str, Any] = {
        "contents": _content_messages_from_request(
            request.messages, system_instruction_key=system_instruction_key
        ),
    }
    if system_instruction := _system_instruction_from_messages(request.messages):
        payload[system_instruction_key] = system_instruction

    generation_config = _generation_config_from_request(request)
    if generation_config:
        payload["generationConfig"] = generation_config

    if request.tools:
        tools = _tools_from_request(request.tools)
        if tools:
            payload["tools"] = tools

    if tool_config := _tool_choice_to_config(request.tool_choice):
        payload["toolConfig"] = tool_config

    return payload


def _parse_google_response(
    response: httpx.Response,
    *,
    provider: str,
) -> NormalizedResponse:
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
    tool_calls: list[NormalizedToolCall] = []
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        if text := part.get("text"):
            text_parts.append(str(text))
            continue
        function_call = part.get("functionCall") or part.get("function_call")
        if isinstance(function_call, dict):
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

    usage_metadata = payload.get("usageMetadata") or {}
    return NormalizedResponse(
        provider=provider,
        model=str(payload.get("model", "")),
        content="\n".join(text_parts) if text_parts else None,
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
