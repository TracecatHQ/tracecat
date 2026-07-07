"""Search text extraction helpers for agent session history."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import orjson
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    ImageUrl,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
    VideoUrl,
)

from tracecat.agent.types import UnifiedMessage
from tracecat.logger import logger

MAX_SEARCH_TEXT_CHARS = 8000
MAX_WINDOW_MESSAGE_CHARS = 1500
_WHITESPACE_RE = re.compile(r"\s+")
_JSON_DUMP_OPTIONS = orjson.OPT_NON_STR_KEYS | orjson.OPT_SORT_KEYS


def _collapse_and_cap(
    text: str, *, max_chars: int = MAX_SEARCH_TEXT_CHARS
) -> str | None:
    collapsed = _WHITESPACE_RE.sub(" ", text).strip()
    if not collapsed:
        return None
    return collapsed[:max_chars]


def _compact_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return orjson.dumps(value, option=_JSON_DUMP_OPTIONS, default=str).decode()
    except Exception:
        try:
            return str(value)
        except Exception:
            return None


def _media_placeholder(kind: str | None, media_type: str | None = None) -> str:
    marker = f"{kind or ''} {media_type or ''}".lower()
    if "image" in marker:
        return "[image]"
    if "audio" in marker:
        return "[audio]"
    if "video" in marker:
        return "[video]"
    if "document" in marker or "pdf" in marker:
        return "[document]"
    return "[binary]"


def _append_if_text(chunks: list[str], value: Any) -> None:
    if isinstance(value, str) and value:
        chunks.append(value)


def _extract_user_content(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, ImageUrl):
        return ["[image]"]
    if isinstance(content, AudioUrl):
        return ["[audio]"]
    if isinstance(content, VideoUrl):
        return ["[video]"]
    if isinstance(content, DocumentUrl):
        return ["[document]"]
    if isinstance(content, BinaryContent):
        return [_media_placeholder("binary", content.media_type)]
    if isinstance(content, Sequence) and not isinstance(content, bytes | bytearray):
        chunks: list[str] = []
        for item in content:
            chunks.extend(_extract_user_content(item))
        return chunks
    return []


_MEDIA_CONTENT_TYPES = (ImageUrl, AudioUrl, VideoUrl, DocumentUrl, BinaryContent)


def _extract_tool_return_content(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    # Media objects must become placeholders, never be JSON-serialized.
    if isinstance(content, _MEDIA_CONTENT_TYPES):
        return _extract_user_content(content)
    if isinstance(content, Sequence) and not isinstance(
        content, str | bytes | bytearray
    ):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, _MEDIA_CONTENT_TYPES):
                chunks.extend(_extract_user_content(item))
            else:
                chunks.extend(_extract_mapping_content_item(item))
        if chunks:
            return chunks
    if isinstance(content, Mapping):
        if mapped := _extract_mapping_content_item(content):
            return mapped
    if compacted := _compact_value(content):
        return [compacted]
    return []


def _extract_pydantic_ai_message(message: ModelRequest | ModelResponse) -> list[str]:
    chunks: list[str] = []
    for part in message.parts:
        if isinstance(part, UserPromptPart):
            chunks.extend(_extract_user_content(part.content))
        elif isinstance(part, TextPart):
            chunks.append(part.content)
        elif isinstance(part, ToolCallPart):
            chunks.append(
                " ".join(
                    value
                    for value in (part.tool_name, _compact_value(part.args))
                    if value
                )
            )
        elif isinstance(part, ToolReturnPart):
            chunks.append(part.tool_name)
            chunks.extend(_extract_tool_return_content(part.content))
    return chunks


def _extract_claude_block(block: Any) -> list[str]:
    if isinstance(block, TextBlock):
        return [block.text]
    if isinstance(block, ToolUseBlock):
        return [
            " ".join(
                value for value in (block.name, _compact_value(block.input)) if value
            )
        ]
    if isinstance(block, ToolResultBlock):
        return _extract_tool_return_content(block.content)
    return []


def _extract_claude_message(message: UnifiedMessage) -> list[str]:
    if isinstance(message, UserMessage):
        if isinstance(message.content, str):
            return [message.content]
        user_chunks: list[str] = []
        for block in message.content:
            user_chunks.extend(_extract_claude_block(block))
        return user_chunks
    if isinstance(message, AssistantMessage):
        assistant_chunks: list[str] = []
        for block in message.content:
            assistant_chunks.extend(_extract_claude_block(block))
        return assistant_chunks
    if isinstance(message, SystemMessage):
        return [text] if isinstance(text := message.data.get("text"), str) else []
    if isinstance(message, ResultMessage):
        return [message.result] if message.result else []
    return []


def extract_search_text(message: UnifiedMessage) -> str | None:
    """Extract capped plain text from a unified agent message for FTS indexing."""
    try:
        if isinstance(message, ModelRequest | ModelResponse):
            chunks = _extract_pydantic_ai_message(message)
        else:
            chunks = _extract_claude_message(message)
        return _collapse_and_cap(" ".join(chunk for chunk in chunks if chunk))
    except Exception as exc:
        logger.debug(
            "Failed to extract agent session search text",
            error_type=type(exc).__name__,
        )
        return None


def _extract_mapping_content(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        chunks: list[str] = []
        for item in value:
            chunks.extend(_extract_mapping_content_item(item))
        return chunks
    if isinstance(value, Mapping):
        return _extract_mapping_content_item(value)
    return []


def _extract_mapping_content_item(item: Any) -> list[str]:
    if isinstance(item, str):
        return [item]
    if not isinstance(item, Mapping):
        return []

    kind = item.get("type") or item.get("kind") or item.get("part_kind")
    # Claude SDK content blocks carry tool calls inline in message.content;
    # route them through the part extractor so tool names/args are indexed.
    if isinstance(kind, str) and kind in {
        "tool-call",
        "tool_use",
        "tool-return",
        "tool_result",
    }:
        return _extract_mapping_part(item)
    if isinstance(kind, str) and kind in {
        "image",
        "image-url",
        "binary",
        "audio",
        "audio-url",
        "video",
        "video-url",
        "document-url",
    }:
        media_type = item.get("media_type") or item.get("mediaType")
        return [
            _media_placeholder(
                kind, media_type if isinstance(media_type, str) else None
            )
        ]

    chunks: list[str] = []
    _append_if_text(chunks, item.get("text"))
    if chunks:
        return chunks
    if "content" in item:
        return _extract_mapping_content(item.get("content"))
    return []


def _extract_mapping_part(part: Any) -> list[str]:
    if not isinstance(part, Mapping):
        return []

    part_kind = part.get("part_kind") or part.get("type") or part.get("kind")
    match part_kind:
        case "user-prompt":
            return _extract_mapping_content(part.get("content"))
        case "text":
            text = part.get("content") or part.get("text")
            return [text] if isinstance(text, str) else []
        case "tool-call" | "tool_use":
            name = part.get("tool_name") or part.get("name")
            args = part.get("args") if "args" in part else part.get("input")
            rendered = " ".join(
                value
                for value in (
                    str(name) if name else None,
                    _compact_value(args),
                )
                if value
            )
            return [rendered] if rendered else []
        case "tool-return" | "tool_result":
            chunks: list[str] = []
            if name := part.get("tool_name"):
                chunks.append(str(name))
            chunks.extend(_extract_mapping_content(part.get("content")))
            return chunks
        case _:
            return _extract_mapping_content_item(part)


def _extract_mapping_message(payload: Mapping[str, Any]) -> list[str]:
    if message := payload.get("message"):
        if isinstance(message, Mapping):
            return _extract_mapping_message(message)

    if parts := payload.get("parts"):
        if isinstance(parts, Sequence) and not isinstance(
            parts, str | bytes | bytearray
        ):
            chunks: list[str] = []
            for part in parts:
                chunks.extend(_extract_mapping_part(part))
            return chunks

    if "content" in payload:
        return _extract_mapping_content(payload.get("content"))

    return []


def extract_search_text_from_history_content(
    content: Mapping[str, Any],
    *,
    max_chars: int = MAX_SEARCH_TEXT_CHARS,
) -> str | None:
    """Extract capped plain text from a persisted AgentSessionHistory JSONB row."""
    try:
        chunks = _extract_mapping_message(content)
        return _collapse_and_cap(
            " ".join(chunk for chunk in chunks if chunk), max_chars=max_chars
        )
    except Exception as exc:
        logger.debug(
            "Failed to extract persisted session search text",
            error_type=type(exc).__name__,
        )
        return None


def infer_message_role(content: Mapping[str, Any], *, kind: str) -> str:
    """Infer a compact display role from a persisted history payload."""
    msg_type = content.get("type")
    if isinstance(msg_type, str) and msg_type in {"user", "assistant", "system"}:
        return msg_type
    message = content.get("message")
    if isinstance(message, Mapping):
        role = message.get("role")
        if isinstance(role, str):
            return role
    payload_kind = content.get("kind")
    if payload_kind == "request":
        return "user"
    if payload_kind == "response":
        return "assistant"
    return kind


def compact_window_text(text: str | None) -> str | None:
    """Collapse and cap a message body for session-window responses."""
    if text is None:
        return None
    return _collapse_and_cap(text, max_chars=MAX_WINDOW_MESSAGE_CHARS)
