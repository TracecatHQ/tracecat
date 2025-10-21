"""Helpers for JSON-safe serialization of agent messages and events."""

from __future__ import annotations

from base64 import b64decode
from binascii import Error as BinasciiError
from typing import Any

from pydantic_core import to_jsonable_python

_BINARY_KIND = "binary"
_BINARY_DATA_KEY = "data"


def serialize_with_base64(value: Any) -> Any:
    """Convert a value to a JSON-serializable structure, base64-encoding bytes."""
    return to_jsonable_python(value, bytes_mode="base64")


def restore_binary_content(value: Any) -> Any:
    """Restore base64-encoded binary payloads to bytes for downstream consumers."""
    if isinstance(value, dict):
        maybe_binary = value.get("kind") == _BINARY_KIND and isinstance(
            value.get(_BINARY_DATA_KEY), str
        )
        items = {}
        for key, inner in value.items():
            if key == _BINARY_DATA_KEY and maybe_binary:
                try:
                    items[key] = b64decode(inner, validate=True)
                except (BinasciiError, ValueError):
                    items[key] = inner
                continue
            items[key] = restore_binary_content(inner)
        return items
    if isinstance(value, list):
        return [restore_binary_content(item) for item in value]
    if isinstance(value, tuple):
        return tuple(restore_binary_content(item) for item in value)
    return value
