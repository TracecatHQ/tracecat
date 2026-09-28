"""Strict helpers for decoding typed values from agent wire payloads."""

from __future__ import annotations

from typing import Any, cast


def required_string(data: dict[str, Any], field_name: str, *, path: str) -> str:
    """Return a required string field or reject the wire payload."""
    value = data.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"{path}.{field_name} must be a JSON string")
    return value


def optional_string(data: dict[str, Any], field_name: str, *, path: str) -> str | None:
    """Return an optional string field or reject the wire payload."""
    value = data.get(field_name)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{path}.{field_name} must be a JSON string")
    return value


def optional_object(
    data: dict[str, Any], field_name: str, *, path: str
) -> dict[str, Any] | None:
    """Return an optional object field or reject the wire payload."""
    value = data.get(field_name)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{path}.{field_name} must be a JSON object")
    return cast(dict[str, Any], value)


def required_object(
    data: dict[str, Any], field_name: str, *, path: str
) -> dict[str, Any]:
    """Return a required object field or reject the wire payload."""
    value = optional_object(data, field_name, path=path)
    if value is None:
        raise ValueError(f"{path}.{field_name} must be a JSON object")
    return value


def optional_integer(data: dict[str, Any], field_name: str, *, path: str) -> int | None:
    """Return an optional non-boolean integer or reject the wire payload."""
    value = data.get(field_name)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"{path}.{field_name} must be a JSON integer")
    return value


def boolean(
    data: dict[str, Any],
    field_name: str,
    *,
    path: str,
    default: bool = False,
) -> bool:
    """Return a boolean field or reject the wire payload."""
    value = data.get(field_name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{path}.{field_name} must be a JSON boolean")
    return value
