"""AWS Bedrock Converse adapter for the Tracecat LLM proxy.

The goal here is clarity more than completeness:

- preserve the current supported Converse path
- make request shaping explicit
- keep model-family specific handling in small helpers
- avoid a giant Bedrock transformation blob
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict
from urllib.parse import quote

import httpx
import orjson
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from botocore.eventstream import EventStreamBuffer
from botocore.loaders import Loader
from botocore.model import ServiceModel
from botocore.parsers import EventStreamJSONParser
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt

from tracecat.agent.llm_proxy.content_blocks import (
    ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY,
)
from tracecat.agent.llm_proxy.provider_common import (
    anthropic_block_stop_event,
    anthropic_message_delta_event,
    anthropic_message_start_event,
    anthropic_signature_delta_event,
    anthropic_text_block_start_event,
    anthropic_text_delta_event,
    anthropic_thinking_block,
    anthropic_thinking_block_start_event,
    anthropic_thinking_delta_event,
    anthropic_tool_block_start_event,
    anthropic_tool_delta_event,
    raise_stream_http_error,
)
from tracecat.agent.llm_proxy.tool_compat import (
    anthropic_tool_choice,
    anthropic_tool_definition,
    create_tool_name_mapping,
    restore_tool_name,
    truncate_tool_call_id,
)
from tracecat.agent.llm_proxy.types import (
    AnthropicStreamEvent,
    NormalizedMessage,
    NormalizedMessagesRequest,
    NormalizedResponse,
    NormalizedToolCall,
    ProviderHTTPRequest,
)
from tracecat.logger import logger

_BEDROCK_MIN_THINKING_BUDGET_TOKENS = 1024
_BEDROCK_STREAM_SHAPE: Any | None = None
_BEDROCK_TOOL_USE_REASONING_ERROR = (
    "Expected thinking or redacted_thinking, but found tool_use"
)
_BEDROCK_FORCED_TOOL_CHOICE_THINKING_ERROR = (
    "Thinking may not be enabled when tool_choice forces tool use."
)
_BEDROCK_UNSUPPORTED_REASONING_EFFORT_ERROR = (
    "reasoning_effort: Extra inputs are not permitted"
)
_BEDROCK_STREAM_EVENT_KEYS = frozenset(
    {
        "messageStart",
        "contentBlockStart",
        "contentBlockDelta",
        "contentBlockStop",
        "metadata",
        "messageStop",
    }
)


@dataclass(slots=True)
class _RetryableBedrockStreamRequestError(RuntimeError):
    """Raised when a failed stream-open request can be rewritten and retried."""


@dataclass(slots=True)
class _BedrockToolStreamState:
    """Track one tool-use block while Bedrock streams partial JSON arguments."""

    tool_call_id: str
    name: str
    started: bool = False


@dataclass(slots=True)
class _BedrockThinkingStreamState:
    """Track whether a thinking block has started and its latest signature."""

    started: bool = False
    signature: str = ""


class _BedrockModelConfigParts(TypedDict):
    """Split Bedrock model settings into Converse config vs model extras."""

    inference_config: dict[str, Any]
    additional_fields: dict[str, Any]


class _BedrockPayloadParts(TypedDict):
    """Structured request fragments before final JSON payload assembly."""

    messages: list[dict[str, Any]]
    system: list[dict[str, Any]]
    inference_config: dict[str, Any]
    additional_fields: dict[str, Any]
    tool_config: dict[str, Any] | None


class _BedrockRequestComponents(TypedDict):
    """Signed-request inputs for one Bedrock Converse operation."""

    url: str
    body: bytes


def _bedrock_payload(parts: _BedrockPayloadParts) -> dict[str, Any]:
    """Assemble the final Bedrock Converse payload from typed fragments."""

    payload: dict[str, Any] = {"messages": parts["messages"]}
    if parts["system"]:
        payload["system"] = parts["system"]
    if parts["inference_config"]:
        payload["inferenceConfig"] = parts["inference_config"]
    if parts["additional_fields"]:
        payload["additionalModelRequestFields"] = parts["additional_fields"]
    if parts["tool_config"]:
        payload["toolConfig"] = parts["tool_config"]
    return payload


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return orjson.dumps(payload)


def _bedrock_response_stream_shape() -> Any:
    """Load and cache the botocore event-stream shape for ConverseStream."""

    global _BEDROCK_STREAM_SHAPE
    if _BEDROCK_STREAM_SHAPE is None:
        bedrock_service_dict = Loader().load_service_model(
            "bedrock-runtime", "service-2"
        )
        service_model = ServiceModel(bedrock_service_dict)
        converse_stream_output = service_model.operation_model(
            "ConverseStream"
        ).output_shape
        if converse_stream_output is None:
            raise RuntimeError("Unable to load Bedrock ConverseStream output shape")
        _BEDROCK_STREAM_SHAPE = converse_stream_output.members["stream"]
    return _BEDROCK_STREAM_SHAPE


def _strip_region_prefix(model: str) -> str:
    for prefix in ("us.", "eu.", "apac."):
        if model.startswith(prefix):
            return model.removeprefix(prefix)
    return model


def _bedrock_base_model(model: str) -> str:
    return _strip_region_prefix(model).lower()


def _is_nova_lite_2_model(model: str) -> bool:
    return "nova-2-lite" in _bedrock_base_model(model)


def _is_claude_model(model: str) -> bool:
    return "claude" in _bedrock_base_model(model)


def _text_block(text: Any) -> dict[str, Any]:
    return {"text": str(text)}


def _data_uri_payload(url: str) -> str:
    """Extract the encoded payload segment from a data URI."""
    header, separator, payload = url.partition(",")
    if not separator or not header.startswith("data:"):
        raise ValueError(
            f"Expected a data URI for Bedrock inline content, got: {url!r}"
        )
    return payload


def _tool_result_content_block(content: Any) -> dict[str, Any]:
    if isinstance(content, str):
        try:
            parsed = orjson.loads(content)
        except orjson.JSONDecodeError:
            return {"text": content}
        if isinstance(parsed, dict):
            return {"json": parsed}
        return {"text": content}

    if isinstance(content, dict):
        if content.get("type") == "text" and content.get("text") is not None:
            return {"text": str(content["text"])}
        return {"json": content}

    if isinstance(content, list):
        text_parts = [
            str(item["text"])
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and item.get("text") is not None
        ]
        if text_parts:
            return {"text": "\n".join(text_parts)}
        return {
            "json": {
                "items": [
                    dict(item) if isinstance(item, dict) else str(item)
                    for item in content
                ]
            }
        }

    return {"text": str(content)}


def _content_item_to_block(item: Any) -> dict[str, Any]:
    match item:
        case {"type": "text", "text": str(text)}:
            return {"text": text}
        case {"type": "thinking", **thinking_block}:
            reasoning_content: dict[str, Any] = {}
            if isinstance(thinking := thinking_block.get("thinking"), str):
                reasoning_content["text"] = thinking
            if isinstance(signature := thinking_block.get("signature"), str):
                reasoning_content["signature"] = signature
            return {"reasoningContent": reasoning_content}
        case {
            "type": "tool_use",
            "id": str(tool_call_id),
            "name": str(name),
            "input": arguments,
        }:
            return {
                "toolUse": {
                    "toolUseId": tool_call_id,
                    "name": name,
                    "input": arguments if isinstance(arguments, dict) else {},
                }
            }
        case {
            "type": "image_url",
            "image_url": {"url": str(url), **image_info},
        }:
            mime_type = image_info.get("format") or "image/png"
            fmt = str(mime_type).removeprefix("image/")
            if not url.startswith("data:"):
                raise ValueError(
                    "Bedrock Converse only supports data: URIs for inline images. "
                    f"HTTP(S) image URLs are not supported: {url!r}"
                )
            return {
                "image": {
                    "format": fmt,
                    "source": {"bytes": _data_uri_payload(url)},
                }
            }
        case {"type": "image_url", "image_url": str(url)}:
            if not url.startswith("data:"):
                raise ValueError(
                    "Bedrock Converse only supports data: URIs for inline images. "
                    f"HTTP(S) image URLs are not supported: {url!r}"
                )
            return {
                "image": {
                    "format": "png",
                    "source": {"bytes": _data_uri_payload(url)},
                }
            }
        case {
            "type": "file",
            "file": {"file_data": str(file_data), **file_info},
        }:
            return {
                "document": {
                    "format": str(file_info.get("format", "pdf")),
                    "name": str(file_info.get("name", "attachment")),
                    "source": {"bytes": file_data},
                }
            }
        case _ if isinstance(item, dict):
            return {"json": item}
        case _:
            return _text_block(item)


def _anthropic_content_blocks_from_message(
    message: NormalizedMessage,
) -> list[Any] | None:
    blocks = message.metadata.get(ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY)
    if isinstance(blocks, list):
        return blocks
    return None


def _message_to_content_blocks(message: NormalizedMessage) -> list[dict[str, Any]]:
    """Convert one normalized message into Bedrock Converse content blocks.

    Bedrock has no separate tool-result message role. Tool results are encoded
    as user-role `toolResult` blocks, while assistant tool calls become
    `toolUse` blocks inside assistant content.
    """

    if message.role == "tool":
        return [
            {
                "toolResult": {
                    "toolUseId": message.tool_call_id or message.name or "tool",
                    "content": [_tool_result_content_block(message.content)],
                }
            }
        ]

    if anthropic_blocks := _anthropic_content_blocks_from_message(message):
        return [_content_item_to_block(item) for item in anthropic_blocks]

    content = message.content
    blocks: list[dict[str, Any]] = []
    if content is None:
        blocks = []
    elif isinstance(content, list):
        for item in content:
            blocks.append(_content_item_to_block(item))
    elif isinstance(content, dict):
        blocks = [_content_item_to_block(content)]
    else:
        blocks = [_text_block(content)]

    blocks.extend(
        {
            "toolUse": {
                "toolUseId": tool_call.id,
                "name": tool_call.name,
                "input": tool_call.arguments
                if isinstance(tool_call.arguments, dict)
                else {},
            }
        }
        for tool_call in message.tool_calls
    )
    return blocks


def _system_blocks(messages: Sequence[NormalizedMessage]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for message in messages:
        if message.role != "system" or message.content is None:
            continue
        if isinstance(message.content, list):
            text_parts = [
                str(item["text"])
                for item in message.content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and item.get("text") is not None
            ]
            if text_parts:
                blocks.append({"text": "\n\n".join(text_parts)})
                continue
        blocks.append({"text": str(message.content)})
    return blocks


def _messages_from_request(
    messages: Sequence[NormalizedMessage],
) -> list[dict[str, Any]]:
    """Build Converse messages, coalescing tool results into user turns.

    Converse expects tool results to be sent back as user content. We therefore
    buffer consecutive tool messages and flush them as a single synthetic user
    message before the next non-tool turn.
    """

    normalized_messages: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_pending_tool_results() -> None:
        if pending_tool_results:
            normalized_messages.append(
                {
                    "role": "user",
                    "content": list(pending_tool_results),
                }
            )
            pending_tool_results.clear()

    for message in messages:
        if message.role == "system":
            continue
        content = _message_to_content_blocks(message)
        if not content:
            continue
        if message.role == "tool":
            pending_tool_results.extend(content)
            continue
        flush_pending_tool_results()
        normalized_messages.append(
            {
                "role": "assistant" if message.role == "assistant" else "user",
                "content": content,
            }
        )
    flush_pending_tool_results()
    if normalized_messages:
        return normalized_messages
    return [{"role": "user", "content": [{"text": " "}]}]


def _tool_spec_from_request_tool(tool: dict[str, Any]) -> dict[str, Any] | None:
    """Translate a normalized tool definition into Bedrock's `toolSpec`."""

    if (definition := anthropic_tool_definition(tool)) is None:
        return None

    tool_spec: dict[str, Any] = {
        "name": definition["name"],
        "inputSchema": {"json": definition["input_schema"]},
    }
    if description := definition.get("description"):
        tool_spec["description"] = description
    return {"toolSpec": tool_spec}


def _build_tool_call_id_mapping(
    messages: Sequence[NormalizedMessage],
) -> dict[str, str]:
    """Precompute truncated tool-call IDs for Bedrock's identifier limits."""

    mapping: dict[str, str] = {}
    for message in messages:
        for tool_call in message.tool_calls:
            truncated = truncate_tool_call_id(tool_call.id)
            if truncated != tool_call.id:
                mapping[tool_call.id] = truncated
        if message.tool_call_id is None:
            continue
        truncated = truncate_tool_call_id(message.tool_call_id)
        if truncated != message.tool_call_id:
            mapping[message.tool_call_id] = truncated
    return mapping


def _apply_tool_mappings(
    payload: dict[str, Any],
    *,
    tool_name_mapping: dict[str, str],
    tool_call_id_mapping: dict[str, str],
) -> None:
    """Rewrite tool names and tool-call IDs everywhere they appear in payload.

    Bedrock validates names and identifiers consistently across tool specs,
    assistant `toolUse` blocks, and user `toolResult` echoes, so a truncation
    pass has to update all three locations together.
    """

    if not tool_name_mapping and not tool_call_id_mapping:
        return
    original_to_truncated = {
        original: truncated for truncated, original in tool_name_mapping.items()
    }

    tool_config = payload.get("toolConfig")
    if isinstance(tool_config, dict):
        tools = tool_config.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                tool_spec = tool.get("toolSpec")
                if not isinstance(tool_spec, dict):
                    continue
                name = tool_spec.get("name")
                if isinstance(name, str) and name in original_to_truncated:
                    tool_spec["name"] = original_to_truncated[name]
        tool_choice = tool_config.get("toolChoice")
        if isinstance(tool_choice, dict):
            tool_choice_tool = tool_choice.get("tool")
            if isinstance(tool_choice_tool, dict):
                name = tool_choice_tool.get("name")
                if isinstance(name, str) and name in original_to_truncated:
                    tool_choice_tool["name"] = original_to_truncated[name]

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            tool_use = item.get("toolUse")
            if isinstance(tool_use, dict):
                name = tool_use.get("name")
                if isinstance(name, str) and name in original_to_truncated:
                    tool_use["name"] = original_to_truncated[name]
                tool_use_id = tool_use.get("toolUseId")
                if isinstance(tool_use_id, str) and tool_use_id in tool_call_id_mapping:
                    tool_use["toolUseId"] = tool_call_id_mapping[tool_use_id]
            tool_result = item.get("toolResult")
            if not isinstance(tool_result, dict):
                continue
            tool_use_id = tool_result.get("toolUseId")
            if isinstance(tool_use_id, str) and tool_use_id in tool_call_id_mapping:
                tool_result["toolUseId"] = tool_call_id_mapping[tool_use_id]


def _restore_tool_call_names(
    tool_calls: list[NormalizedToolCall],
    mapping: dict[str, str],
) -> list[NormalizedToolCall]:
    if not mapping or not tool_calls:
        return tool_calls
    return [
        NormalizedToolCall(
            id=tool_call.id,
            name=restore_tool_name(tool_call.name, mapping) or "",
            arguments=tool_call.arguments,
            type=tool_call.type,
        )
        for tool_call in tool_calls
    ]


def _dummy_tool_spec() -> dict[str, Any]:
    return {
        "toolSpec": {
            "name": "dummy_tool",
            "description": "This is a dummy tool call",
            "inputSchema": {"json": {"type": "object", "properties": {}}},
        }
    }


def _messages_require_tool_config(messages: Sequence[NormalizedMessage]) -> bool:
    return any(message.tool_calls or message.role == "tool" for message in messages)


def _bedrock_tool_choice(tool_choice: Any) -> dict[str, Any] | None:
    if (anthropic_choice := anthropic_tool_choice(tool_choice)) is None:
        return None
    match anthropic_choice["type"]:
        case "tool":
            return {"tool": {"name": anthropic_choice["name"]}}
        case "any":
            return {"any": {}}
        case "auto":
            return {"auto": {}}
        case "none":
            return None
        case _:
            return None


def _reasoning_budget(reasoning_effort: str) -> int:
    match reasoning_effort.lower():
        case "minimal" | "low":
            return _BEDROCK_MIN_THINKING_BUDGET_TOKENS
        case "medium":
            return 2048
        case "high":
            return 4096
        case _:
            return _BEDROCK_MIN_THINKING_BUDGET_TOKENS


def _inference_config_from_request(
    request: NormalizedMessagesRequest,
    *,
    target_model: str,
) -> _BedrockModelConfigParts:
    """Split generic settings into Converse inference config vs model extras.

    Bedrock exposes basic generation knobs under `inferenceConfig`, while
    provider/model-family specific reasoning settings live under
    `additionalModelRequestFields`.
    """

    inference_config: dict[str, Any] = {}
    additional_fields: dict[str, Any] = {}
    settings = dict(request.model_settings)

    for source_key, target_key in (
        ("max_completion_tokens", "maxTokens"),
        ("max_tokens", "maxTokens"),
        ("temperature", "temperature"),
        ("top_p", "topP"),
        ("top_k", "topK"),
    ):
        if (value := settings.get(source_key)) is not None:
            inference_config[target_key] = value

    if stop_sequences := settings.get("stop"):
        if isinstance(stop_sequences, str):
            inference_config["stopSequences"] = [stop_sequences]
        elif isinstance(stop_sequences, list):
            inference_config["stopSequences"] = stop_sequences

    if reasoning_effort := settings.get("reasoning_effort"):
        if _is_nova_lite_2_model(target_model):
            additional_fields["reasoningConfig"] = {
                "type": "enabled",
                "maxReasoningEffort": str(reasoning_effort),
            }
        elif _is_claude_model(target_model):
            additional_fields["thinking"] = {
                "type": "enabled",
                "budget_tokens": _reasoning_budget(str(reasoning_effort)),
            }

    if thinking := settings.get("thinking"):
        additional_fields["thinking"] = thinking

    return {
        "inference_config": inference_config,
        "additional_fields": additional_fields,
    }


def _tool_config_from_request(
    request: NormalizedMessagesRequest,
) -> dict[str, Any] | None:
    """Build Bedrock `toolConfig`, inserting a dummy tool when replay needs it.

    Converse rejects tool-result echoes unless the request advertises tool
    support. When history contains prior tool use but the current request does
    not declare tools, we add a harmless dummy spec to keep the replay valid.
    """

    tools = [
        tool_spec
        for tool in request.tools
        if (tool_spec := _tool_spec_from_request_tool(tool)) is not None
    ]
    if not tools and _messages_require_tool_config(request.messages):
        tools = [_dummy_tool_spec()]
    if not tools and request.tool_choice is None:
        return None

    tool_config: dict[str, Any] = {}
    if tools:
        tool_config["tools"] = tools
    if tool_choice := _bedrock_tool_choice(request.tool_choice):
        tool_config["toolChoice"] = tool_choice
    return tool_config or None


def _normalize_reasoning_tool_choice(
    tool_config: dict[str, Any] | None,
    *,
    additional_fields: dict[str, Any],
) -> dict[str, Any]:
    """Drop Claude thinking when Bedrock also forces tool use.

    Bedrock's Anthropic models reject requests that combine thinking with a
    forced tool policy. In that conflict we keep the caller's explicit tool
    choice and remove thinking from the extra model fields.
    """

    if tool_config is None or "thinking" not in additional_fields:
        return additional_fields
    if "tools" not in tool_config:
        return additional_fields
    tool_choice = tool_config.get("toolChoice")
    if not isinstance(tool_choice, dict):
        return additional_fields
    if "tool" not in tool_choice and "any" not in tool_choice:
        return additional_fields

    # Bedrock rejects forced tool use when Anthropic thinking is enabled.
    # Preserve the explicit tool choice and drop thinking instead of weakening
    # the caller's tool policy.
    rewritten_fields = dict(additional_fields)
    rewritten_fields.pop("thinking", None)
    return rewritten_fields


def _content_text_from_response(content: Any) -> list[str]:
    if not isinstance(content, list):
        return []
    text_parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if text := item.get("text"):
            text_parts.append(str(text))
    return text_parts


def _content_blocks_from_response(content: Any) -> list[dict[str, Any]]:
    """Extract Anthropic-compatible text/thinking blocks from Converse output."""

    if not isinstance(content, list):
        return []
    content_blocks: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if isinstance(text := _bedrock_case_get(item, "text", "Text"), str):
            content_blocks.append({"type": "text", "text": text})
            continue
        reasoning_content = _bedrock_case_get(
            item, "reasoningContent", "ReasoningContent"
        )
        if not isinstance(reasoning_content, dict):
            continue

        thinking = ""
        if isinstance(
            reasoning_text := _bedrock_case_get(reasoning_content, "text", "Text"), str
        ):
            thinking = reasoning_text

        signature = ""
        if isinstance(
            reasoning_signature := _bedrock_case_get(
                reasoning_content, "signature", "Signature"
            ),
            str,
        ):
            signature = reasoning_signature

        content_blocks.append(
            anthropic_thinking_block(thinking=thinking, signature=signature)
        )
    return content_blocks


def _tool_calls_from_response(content: Any) -> list[NormalizedToolCall]:
    if not isinstance(content, list):
        return []
    tool_calls: list[NormalizedToolCall] = []
    for index, item in enumerate(content):
        if not isinstance(item, dict):
            continue
        tool_use = item.get("toolUse")
        if not isinstance(tool_use, dict):
            continue
        name = str(tool_use.get("name", ""))
        tool_calls.append(
            NormalizedToolCall(
                id=str(tool_use.get("toolUseId", f"{name or 'tool'}-{index}")),
                name=name,
                arguments=tool_use.get("input") or {},
            )
        )
    return tool_calls


def _parse_bedrock_response(
    response: httpx.Response,
    *,
    provider: str,
    tool_name_mapping: dict[str, str],
) -> NormalizedResponse:
    """Normalize a non-streaming Converse response into proxy response types."""

    if response.status_code >= 400:
        detail = response.text[:1000]
        raise httpx.HTTPStatusError(
            f"bedrock provider error: {response.status_code} {detail}",
            request=response.request,
            response=response,
        )

    payload = response.json()
    output = payload.get("output") or {}
    message = output.get("message") or {}
    content = message.get("content") or []
    content_blocks = _content_blocks_from_response(content)
    text_parts = _content_text_from_response(content)
    tool_calls = _restore_tool_call_names(
        _tool_calls_from_response(content),
        tool_name_mapping,
    )
    usage = payload.get("usage") or {}
    return NormalizedResponse(
        provider=provider,
        model=str(payload.get("modelId", "")),
        content=(
            content_blocks
            if any(block.get("type") == "thinking" for block in content_blocks)
            else "\n".join(text_parts)
            if text_parts
            else None
        ),
        tool_calls=tuple(tool_calls),
        finish_reason=str(payload.get("stopReason"))
        if payload.get("stopReason")
        else None,
        usage={
            "input_tokens": int(usage.get("inputTokens", 0)),
            "output_tokens": int(usage.get("outputTokens", 0)),
        },
        raw=payload,
    )


def _bedrock_request_components(
    request: NormalizedMessagesRequest,
    credentials: dict[str, str],
    *,
    stream: bool,
) -> _BedrockRequestComponents:
    """Assemble the signed-request inputs for Converse or ConverseStream."""

    region = credentials.get("AWS_REGION")
    if not region:
        raise ValueError("Bedrock requires AWS_REGION")

    model = credentials.get("AWS_INFERENCE_PROFILE_ID") or credentials.get(
        "AWS_MODEL_ID"
    )
    if not model:
        raise ValueError("Bedrock requires AWS_INFERENCE_PROFILE_ID or AWS_MODEL_ID")

    base_url = (
        request.base_url or f"https://bedrock-runtime.{region}.amazonaws.com"
    ).rstrip("/")
    operation = "converse-stream" if stream else "converse"
    encoded_model = quote(model, safe="")
    url = f"{base_url}/model/{encoded_model}/{operation}"

    model_config = _inference_config_from_request(
        request,
        target_model=model,
    )
    tool_config = _tool_config_from_request(request)
    payload = _bedrock_payload(
        {
            "messages": _messages_from_request(request.messages),
            "system": _system_blocks(request.messages),
            "inference_config": model_config["inference_config"],
            "additional_fields": _normalize_reasoning_tool_choice(
                tool_config,
                additional_fields=model_config["additional_fields"],
            ),
            "tool_config": tool_config,
        }
    )

    _apply_tool_mappings(
        payload,
        tool_name_mapping=create_tool_name_mapping(request.tools),
        tool_call_id_mapping=_build_tool_call_id_mapping(request.messages),
    )

    return {"url": url, "body": _json_bytes(payload)}


def _signed_bedrock_request(
    *,
    request: NormalizedMessagesRequest,
    credentials: dict[str, str],
    url: str,
    body: bytes,
) -> ProviderHTTPRequest:
    region = credentials.get("AWS_REGION")
    if not region:
        raise ValueError("Bedrock requires AWS_REGION")

    if api_key := credentials.get("AWS_BEARER_TOKEN_BEDROCK"):
        return ProviderHTTPRequest(
            method="POST",
            url=url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            body=body,
            stream=request.stream,
        )

    access_key = credentials.get("AWS_ACCESS_KEY_ID")
    secret_key = credentials.get("AWS_SECRET_ACCESS_KEY")
    if not access_key or not secret_key:
        raise ValueError(
            "Bedrock requires AWS_BEARER_TOKEN_BEDROCK or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY"
        )

    aws_request = AWSRequest(
        method="POST",
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    credentials_obj = Credentials(
        access_key,
        secret_key,
        credentials.get("AWS_SESSION_TOKEN"),
    )
    SigV4Auth(credentials_obj, "bedrock", region).add_auth(aws_request)
    headers = dict(aws_request.headers.items())
    headers["Content-Type"] = "application/json"
    return ProviderHTTPRequest(
        method="POST",
        url=url,
        headers=headers,
        body=body,
        stream=request.stream,
    )


def _load_request_body(request: ProviderHTTPRequest) -> dict[str, Any]:
    if request.json_body is not None:
        return dict(request.json_body)
    if request.body:
        loaded = orjson.loads(request.body)
        if isinstance(loaded, dict):
            return loaded
    return {}


def _remove_thinking_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of payload without Bedrock Claude `thinking` extras."""

    rewritten = dict(payload)
    additional = rewritten.get("additionalModelRequestFields")
    if not isinstance(additional, dict) or "thinking" not in additional:
        return rewritten
    rewritten_additional = dict(additional)
    rewritten_additional.pop("thinking", None)
    if rewritten_additional:
        rewritten["additionalModelRequestFields"] = rewritten_additional
    else:
        rewritten.pop("additionalModelRequestFields", None)
    return rewritten


def _remove_reasoning_effort_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of payload without Bedrock Nova `reasoning_effort` extras."""

    rewritten = dict(payload)
    additional = rewritten.get("additionalModelRequestFields")
    if not isinstance(additional, dict) or "reasoning_effort" not in additional:
        return rewritten
    rewritten_additional = dict(additional)
    rewritten_additional.pop("reasoning_effort", None)
    if rewritten_additional:
        rewritten["additionalModelRequestFields"] = rewritten_additional
    else:
        rewritten.pop("additionalModelRequestFields", None)
    return rewritten


def _parsed_bedrock_stream_event(
    raw_event: Any,
    *,
    parser: EventStreamJSONParser,
) -> dict[str, Any] | None:
    """Decode one event-stream frame into a Converse event payload.

    Bedrock can send either structured event-stream members or a JSON blob
    inside `chunk.bytes`. This helper hides that split and raises a bounded
    error message for stream-open failures.
    """

    response_dict = raw_event.to_response_dict()
    parsed = parser.parse(response_dict, _bedrock_response_stream_shape())
    if isinstance(parsed, dict) and any(
        key in parsed for key in _BEDROCK_STREAM_EVENT_KEYS
    ):
        return parsed
    if response_dict["status_code"] >= 400:
        detail = response_dict["body"].decode("utf-8", errors="ignore")
        exception_type = response_dict["headers"].get(":exception-type")
        message = f"{exception_type} {detail}".strip() if exception_type else detail
        raise RuntimeError(
            f"bedrock provider error: {response_dict['status_code']} {message[:512]}"
        )
    chunk = parsed.get("chunk")
    if not isinstance(chunk, dict):
        body = response_dict.get("body")
        if not isinstance(body, bytes):
            return None
        loaded = orjson.loads(body)
        return loaded if isinstance(loaded, dict) else None
    chunk_bytes = chunk.get("bytes")
    if not isinstance(chunk_bytes, (bytes, bytearray)):
        return None
    loaded = orjson.loads(bytes(chunk_bytes))
    return loaded if isinstance(loaded, dict) else None


def _bedrock_case_get(payload: dict[str, Any], snake_key: str, pascal_key: str) -> Any:
    if snake_key in payload:
        return payload[snake_key]
    return payload.get(pascal_key)


def _bedrock_json_payload_error(payload: Any) -> str | None:
    """Extract a readable error string from Bedrock's JSON-style error payloads."""

    if not isinstance(payload, dict):
        return None
    if isinstance(error := payload.get("Error"), dict):
        code = str(error.get("Code") or "").strip()
        message = str(error.get("Message") or "").strip()
        if code or message:
            return f"{code}: {message}".strip(": ")

    output = _bedrock_case_get(payload, "output", "Output")
    if not isinstance(output, dict):
        return None
    error_type = output.get("__type")
    message = _bedrock_case_get(output, "message", "Message")
    if isinstance(error_type, str):
        detail = str(message).strip() if message is not None else ""
        return f"{error_type}: {detail}".strip(": ")
    return None


def _bedrock_json_payload_to_events(payload: Any) -> list[dict[str, Any]]:
    """Flatten JSON payloads into Converse-style event dictionaries."""

    if isinstance(payload, list):
        extracted_events: list[dict[str, Any]] = []
        for item in payload:
            extracted_events.extend(_bedrock_json_payload_to_events(item))
        return extracted_events

    if not isinstance(payload, dict):
        return []

    if any(key in payload for key in _BEDROCK_STREAM_EVENT_KEYS):
        return [payload]

    for wrapper_key in ("stream", "events"):
        if wrapper_key in payload:
            wrapped = payload[wrapper_key]
            wrapped_events = _bedrock_json_payload_to_events(wrapped)
            if wrapped_events:
                return wrapped_events

    output = _bedrock_case_get(payload, "output", "Output")
    if not isinstance(output, dict):
        return []
    message = _bedrock_case_get(output, "message", "Message")
    content = (
        _bedrock_case_get(message, "content", "Content")
        if isinstance(message, dict)
        else None
    )
    usage = _bedrock_case_get(payload, "usage", "Usage")
    stop_reason = _bedrock_case_get(payload, "stopReason", "StopReason")

    if not isinstance(content, list):
        return []

    synthesized_events: list[dict[str, Any]] = [
        {"messageStart": {"conversationId": "bedrock-json"}}
    ]
    for index, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        if isinstance(text := _bedrock_case_get(block, "text", "Text"), str):
            synthesized_events.extend(
                [
                    {"contentBlockStart": {"contentBlockIndex": index, "start": {}}},
                    {
                        "contentBlockDelta": {
                            "contentBlockIndex": index,
                            "delta": {"text": text},
                        }
                    },
                    {"contentBlockStop": {"contentBlockIndex": index}},
                ]
            )
            continue

        tool_use = _bedrock_case_get(block, "toolUse", "ToolUse")
        if isinstance(tool_use, dict):
            tool_input = _bedrock_case_get(tool_use, "input", "Input")
            synthesized_events.extend(
                [
                    {
                        "contentBlockStart": {
                            "contentBlockIndex": index,
                            "start": {
                                "toolUse": {
                                    "toolUseId": str(
                                        _bedrock_case_get(
                                            tool_use, "toolUseId", "ToolUseId"
                                        )
                                        or f"tool_{index}"
                                    ),
                                    "name": str(
                                        _bedrock_case_get(tool_use, "name", "Name")
                                        or ""
                                    ),
                                }
                            },
                        }
                    },
                    {
                        "contentBlockDelta": {
                            "contentBlockIndex": index,
                            "delta": {
                                "toolUse": {
                                    "input": (
                                        orjson.dumps(tool_input).decode("utf-8")
                                        if tool_input is not None
                                        else "{}"
                                    )
                                }
                            },
                        }
                    },
                    {"contentBlockStop": {"contentBlockIndex": index}},
                ]
            )
            continue

        reasoning_content = _bedrock_case_get(
            block, "reasoningContent", "ReasoningContent"
        )
        if isinstance(reasoning_content, dict):
            start: dict[str, Any] = {
                "contentBlockIndex": index,
                "start": {"reasoningContent": {}},
            }
            delta: dict[str, Any] = {
                "contentBlockIndex": index,
                "delta": {"reasoningContent": {}},
            }
            if isinstance(
                text := _bedrock_case_get(reasoning_content, "text", "Text"), str
            ):
                delta["delta"]["reasoningContent"]["text"] = text
            if isinstance(
                signature := _bedrock_case_get(
                    reasoning_content, "signature", "Signature"
                ),
                str,
            ):
                delta["delta"]["reasoningContent"]["signature"] = signature
            synthesized_events.append({"contentBlockStart": start})
            if delta["delta"]["reasoningContent"]:
                synthesized_events.append({"contentBlockDelta": delta})
            synthesized_events.append(
                {"contentBlockStop": {"contentBlockIndex": index}}
            )

    if isinstance(usage, dict):
        synthesized_events.append({"metadata": {"usage": usage}})
    if stop_reason is not None:
        synthesized_events.append({"messageStop": {"stopReason": stop_reason}})
    return synthesized_events


async def _iter_bedrock_stream_events(
    response: httpx.Response,
) -> AsyncIterator[dict[str, Any]]:
    parser = EventStreamJSONParser()
    buffer = EventStreamBuffer()
    chunk_prefix = bytearray()
    json_chunks: list[bytes] | None = None
    async for chunk in response.aiter_bytes():
        if json_chunks is not None:
            json_chunks.append(chunk)
            continue
        if len(chunk_prefix) < 32:
            chunk_prefix.extend(chunk[: 32 - len(chunk_prefix)])
        prefix = bytes(chunk_prefix).lstrip()
        if prefix.startswith((b"{", b"[")):
            logger.warning(
                "Bedrock stream returned JSON payload instead of eventstream",
                content_type=response.headers.get("content-type"),
                preview=prefix[:32].decode("utf-8", errors="ignore"),
            )
            json_chunks = [chunk]
            continue
        buffer.add_data(chunk)
        for raw_event in buffer:
            if event := _parsed_bedrock_stream_event(raw_event, parser=parser):
                yield event
    if json_chunks is not None:
        raw_json = b"".join(json_chunks)
        payload = orjson.loads(raw_json)
        if error_message := _bedrock_json_payload_error(payload):
            raise RuntimeError(f"bedrock provider error: {error_message[:512]}")
        for event in _bedrock_json_payload_to_events(payload):
            yield event


@dataclass(slots=True)
class BedrockAdapter:
    """Adapter for AWS Bedrock Converse API."""

    provider: str = "bedrock"

    def prepare_request(
        self, request: NormalizedMessagesRequest, credentials: dict[str, str]
    ) -> ProviderHTTPRequest:
        request_components = _bedrock_request_components(
            request,
            credentials,
            stream=False,
        )
        return _signed_bedrock_request(
            request=request,
            credentials=credentials,
            url=request_components["url"],
            body=request_components["body"],
        )

    async def parse_response(
        self, response: httpx.Response, request: NormalizedMessagesRequest
    ) -> NormalizedResponse:
        return _parse_bedrock_response(
            response,
            provider=self.provider,
            tool_name_mapping=create_tool_name_mapping(request.tools),
        )

    def prepare_retry_request(
        self,
        *,
        response: httpx.Response,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
        outbound_request: ProviderHTTPRequest,
    ) -> ProviderHTTPRequest | None:
        del credentials, request
        payload = _load_request_body(outbound_request)
        rewritten = payload
        if (
            _BEDROCK_TOOL_USE_REASONING_ERROR in response.text
            or _BEDROCK_FORCED_TOOL_CHOICE_THINKING_ERROR in response.text
        ):
            rewritten = _remove_thinking_from_payload(rewritten)
        if _BEDROCK_UNSUPPORTED_REASONING_EFFORT_ERROR in response.text:
            rewritten = _remove_reasoning_effort_from_payload(rewritten)
        if rewritten == payload:
            return None
        return ProviderHTTPRequest(
            method=outbound_request.method,
            url=outbound_request.url,
            headers=outbound_request.headers,
            body=_json_bytes(rewritten),
            stream=outbound_request.stream,
        )

    async def stream_anthropic(
        self,
        client: httpx.AsyncClient,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
    ) -> AsyncIterator[AnthropicStreamEvent]:
        request_components = _bedrock_request_components(
            request,
            credentials,
            stream=True,
        )
        stream_request = _signed_bedrock_request(
            request=request,
            credentials=credentials,
            url=request_components["url"],
            body=request_components["body"],
        )
        stream_context: Any | None = None
        response: httpx.Response | None = None

        async def _open_stream_response() -> tuple[Any, httpx.Response]:
            nonlocal stream_request
            opened_context = client.stream(
                stream_request.method,
                stream_request.url,
                headers=stream_request.headers,
                content=stream_request.body,
            )
            opened_response = await opened_context.__aenter__()
            try:
                logger.debug(
                    "Bedrock stream response opened",
                    status_code=opened_response.status_code,
                    content_type=opened_response.headers.get("content-type"),
                    transfer_encoding=opened_response.headers.get("transfer-encoding"),
                    provider=self.provider,
                    model=request.model,
                )
                if opened_response.status_code >= 400:
                    await opened_response.aread()
                    retry_request = self.prepare_retry_request(
                        response=opened_response,
                        request=request,
                        credentials=credentials,
                        outbound_request=stream_request,
                    )
                    if retry_request is not None:
                        stream_request = retry_request
                        raise _RetryableBedrockStreamRequestError
                    await raise_stream_http_error(
                        opened_response,
                        provider=self.provider,
                    )
                return opened_context, opened_response
            except Exception as exc:
                await opened_context.__aexit__(type(exc), exc, exc.__traceback__)
                raise

        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(_RetryableBedrockStreamRequestError),
            stop=stop_after_attempt(2),
            reraise=True,
        ):
            with attempt:
                stream_context, response = await _open_stream_response()

        if stream_context is None or response is None:
            raise RuntimeError("Bedrock stream failed to open")

        try:
            yielded_message_start = False
            tool_name_mapping = create_tool_name_mapping(request.tools)
            content_types: dict[int, str] = {}
            tool_states: dict[int, _BedrockToolStreamState] = {}
            thinking_states: dict[int, _BedrockThinkingStreamState] = {}
            output_tokens = 0
            pending_stop_reason: str | None = None

            async for event in _iter_bedrock_stream_events(response):
                if "messageStart" in event and not yielded_message_start:
                    yielded_message_start = True
                    conversation_id = event["messageStart"].get(
                        "conversationId", "bedrock"
                    )
                    yield anthropic_message_start_event(
                        message_id=f"msg_{conversation_id}",
                        model=request.model,
                    )
                    continue

                if "contentBlockStart" in event:
                    start_event = event["contentBlockStart"]
                    if not isinstance(start_event, dict):
                        continue
                    index = int(start_event.get("contentBlockIndex", 0))
                    start = start_event.get("start")
                    if not isinstance(start, dict):
                        continue
                    if isinstance(tool_use := start.get("toolUse"), dict):
                        tool_call_id = str(tool_use.get("toolUseId", f"tool_{index}"))
                        name = (
                            restore_tool_name(
                                str(tool_use.get("name", "")),
                                tool_name_mapping,
                            )
                            or ""
                        )
                        content_types[index] = "tool"
                        tool_states[index] = _BedrockToolStreamState(
                            tool_call_id=tool_call_id,
                            name=name,
                            started=True,
                        )
                        yield anthropic_tool_block_start_event(
                            index=index,
                            tool_call_id=tool_call_id,
                            name=name,
                        )
                        continue
                    if "reasoningContent" in start:
                        content_types[index] = "thinking"
                        thinking_states[index] = _BedrockThinkingStreamState(
                            started=True
                        )
                        yield anthropic_thinking_block_start_event(index)
                        reasoning_content = start.get("reasoningContent")
                        if isinstance(reasoning_content, dict):
                            if (
                                isinstance(text := reasoning_content.get("text"), str)
                                and text
                            ):
                                yield anthropic_thinking_delta_event(index, text)
                            if (
                                isinstance(
                                    signature := reasoning_content.get("signature"),
                                    str,
                                )
                                and signature
                            ):
                                yield anthropic_signature_delta_event(index, signature)
                        continue
                    content_types[index] = "text"
                    yield anthropic_text_block_start_event(index)
                    continue

                if "contentBlockDelta" in event:
                    delta_event = event["contentBlockDelta"]
                    if not isinstance(delta_event, dict):
                        continue
                    index = int(delta_event.get("contentBlockIndex", 0))
                    delta = delta_event.get("delta")
                    if not isinstance(delta, dict):
                        continue
                    if isinstance(text := delta.get("text"), str):
                        if content_types.get(index) != "text":
                            content_types[index] = "text"
                            yield anthropic_text_block_start_event(index)
                        yield anthropic_text_delta_event(index, text)
                        continue
                    if isinstance(tool_use := delta.get("toolUse"), dict):
                        state = tool_states.get(index)
                        if state is None:
                            state = _BedrockToolStreamState(
                                tool_call_id=f"tool_{index}",
                                name="",
                            )
                            tool_states[index] = state
                        if not state.started:
                            state.started = True
                            content_types[index] = "tool"
                            yield anthropic_tool_block_start_event(
                                index=index,
                                tool_call_id=state.tool_call_id,
                                name=state.name,
                            )
                        if isinstance(partial_json := tool_use.get("input"), str):
                            yield anthropic_tool_delta_event(index, partial_json)
                        continue
                    if isinstance(
                        reasoning_content := delta.get("reasoningContent"), dict
                    ):
                        state = thinking_states.get(index)
                        if state is None:
                            state = _BedrockThinkingStreamState()
                            thinking_states[index] = state
                        if not state.started:
                            state.started = True
                            content_types[index] = "thinking"
                            yield anthropic_thinking_block_start_event(index)
                        if (
                            isinstance(text := reasoning_content.get("text"), str)
                            and text
                        ):
                            yield anthropic_thinking_delta_event(index, text)
                        if (
                            isinstance(
                                signature := reasoning_content.get("signature"), str
                            )
                            and signature
                        ):
                            state.signature += signature
                            yield anthropic_signature_delta_event(index, signature)
                        continue

                if "contentBlockStop" in event:
                    stop_event = event["contentBlockStop"]
                    if not isinstance(stop_event, dict):
                        continue
                    index = int(stop_event.get("contentBlockIndex", 0))
                    yield anthropic_block_stop_event(index)
                    continue

                if "metadata" in event:
                    metadata = event["metadata"]
                    if not isinstance(metadata, dict):
                        continue
                    usage = metadata.get("usage")
                    if isinstance(usage, dict):
                        output_tokens = int(usage.get("outputTokens", output_tokens))
                    continue

                if "messageStop" in event:
                    stop_event = event["messageStop"]
                    if not isinstance(stop_event, dict):
                        continue
                    pending_stop_reason = str(
                        stop_event.get("stopReason") or "end_turn"
                    )
                    continue

            if not yielded_message_start:
                yield anthropic_message_start_event(
                    message_id="msg_bedrock",
                    model=request.model,
                )
            yield anthropic_message_delta_event(
                stop_reason=pending_stop_reason or "end_turn",
                output_tokens=output_tokens,
            )
            yield AnthropicStreamEvent("message_stop", {"type": "message_stop"})
        finally:
            await stream_context.__aexit__(None, None, None)


__all__ = ["BedrockAdapter"]
