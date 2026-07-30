"""Persistence helpers for agent session history payloads."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, cast

import orjson

_ENCODED_JSONB_KEY_PREFIX = "__tracecat_encoded_key_v1__:"


@dataclass(frozen=True, slots=True)
class PreparedSessionHistory:
    """Raw resume data and its PostgreSQL-safe JSONB projection."""

    content: dict[str, Any]
    raw_session_line: bytes | None


@dataclass(frozen=True, slots=True)
class SessionHistoryContent:
    """Decoded session history content and its exact raw JSONL line, if stored."""

    content: dict[str, Any]
    raw_line: str | None


def _jsonb_safe_key(key: object) -> tuple[object, bool]:
    if not isinstance(key, str):
        return key, False
    if "\x00" not in key and not key.startswith(_ENCODED_JSONB_KEY_PREFIX):
        return key, False

    # Encoding the whole key avoids collisions with a literal "\\u0000" key.
    # Keys already in our namespace are encoded too, keeping the mapping injective.
    encoded = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii")
    return f"{_ENCODED_JSONB_KEY_PREFIX}{encoded}", True


def _jsonb_safe_value(value: object) -> tuple[object, bool]:
    match value:
        case str() as text:
            safe_text = text.replace("\x00", "\\u0000")
            return safe_text, safe_text != text
        case list() as items:
            safe_items: list[object] = []
            changed = False
            for item in items:
                safe_item, item_changed = _jsonb_safe_value(item)
                safe_items.append(safe_item)
                changed = changed or item_changed
            return (safe_items if changed else items), changed
        case dict() as mapping:
            safe_mapping: dict[object, object] = {}
            changed = False
            for key, item in mapping.items():
                safe_key, key_changed = _jsonb_safe_key(key)
                safe_item, item_changed = _jsonb_safe_value(item)
                safe_mapping[safe_key] = safe_item
                changed = changed or key_changed or item_changed
            return (safe_mapping if changed else mapping), changed
        case _:
            return value, False


def prepare_session_history(
    content: dict[str, Any],
    *,
    raw_session_line: str | bytes | None = None,
) -> PreparedSessionHistory:
    """Prepare exact resume bytes and a JSONB-safe history projection.

    Raw bytes are retained only when the projection must change. Ordinary
    session rows continue using the existing JSONB-only storage path.
    """
    safe_value, changed = _jsonb_safe_value(content)
    safe_content = cast(dict[str, Any], safe_value)
    if not changed:
        return PreparedSessionHistory(content=safe_content, raw_session_line=None)

    if isinstance(raw_session_line, str):
        raw_bytes = raw_session_line.encode("utf-8")
    elif raw_session_line is not None:
        raw_bytes = raw_session_line
    else:
        raw_bytes = orjson.dumps(content)

    return PreparedSessionHistory(
        content=safe_content,
        raw_session_line=raw_bytes,
    )


def decode_raw_session_line(
    raw_session_line: bytes | bytearray | memoryview,
) -> SessionHistoryContent:
    """Decode an exact JSONL row stored alongside its JSONB projection."""
    raw_line = bytes(raw_session_line).decode("utf-8")
    decoded = orjson.loads(raw_line)
    if not isinstance(decoded, dict):
        raise ValueError("Raw agent session line must be a JSON object")
    return SessionHistoryContent(
        content=cast(dict[str, Any], decoded),
        raw_line=raw_line,
    )
