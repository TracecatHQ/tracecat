"""Canonical serialization helpers for workspace sync specs."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

import orjson
from pydantic import BaseModel
from pydantic_core import to_jsonable_python

CANONICAL_HASH_VERSION = 1


def canonical_data(value: Any) -> Any:
    """Convert a value into JSON-compatible data with omitted null model fields."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return to_jsonable_python(value, fallback=str)


def canonical_hash_data(value: Any) -> Any:
    """Convert a value into stable hash data, ignoring model defaults."""
    if isinstance(value, BaseModel):
        return value.model_dump(
            mode="json",
            exclude_none=True,
            exclude_defaults=True,
        )
    return to_jsonable_python(value, fallback=str)


def canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    """Serialize JSON deterministically."""
    option = orjson.OPT_SORT_KEYS
    if pretty:
        option |= orjson.OPT_INDENT_2
    return orjson.dumps(canonical_data(value), option=option)


def canonical_json_text(value: Any, *, pretty: bool = True) -> str:
    """Serialize JSON deterministically as UTF-8 text with a trailing newline."""
    return canonical_json_bytes(value, pretty=pretty).decode("utf-8") + "\n"


def stable_hash(value: Any) -> str:
    """Return a versioned SHA-256 hash for canonical sync comparisons."""
    payload = orjson.dumps(canonical_hash_data(value), option=orjson.OPT_SORT_KEYS)
    digest = sha256(payload).hexdigest()
    return f"v{CANONICAL_HASH_VERSION}:{digest}"
