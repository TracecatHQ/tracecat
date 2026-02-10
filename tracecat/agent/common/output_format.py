"""Lightweight output format helpers for the Claude SDK runtime.

This module has NO pydantic-ai dependencies and can be safely imported
in sandboxed runtimes with minimal import footprint.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from tracecat.logger import logger


class _ItemsSchema(TypedDict):
    type: str


class _JsonSchema(TypedDict):
    type: str
    items: NotRequired[_ItemsSchema]


# JSON Schema mappings for primitive output types
_PRIMITIVE_JSON_SCHEMAS: dict[str, _JsonSchema] = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "list[str]": {"type": "array", "items": {"type": "string"}},
    "list[int]": {"type": "array", "items": {"type": "integer"}},
    "list[float]": {"type": "array", "items": {"type": "number"}},
    "list[bool]": {"type": "array", "items": {"type": "boolean"}},
}


def build_sdk_output_format(
    output_type: str | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Convert Tracecat's output_type into Claude SDK's output_format shape.

    The Claude SDK expects ``output_format`` as::

        {"type": "json_schema", "schema": <json_schema_dict>}

    For dict output_type (user-provided JSON schema), we use it directly.
    For primitive strings ("int", "str", etc.), we wrap in a
    ``{"type": "object", "properties": {"result": <primitive>}, ...}``
    envelope — same wrapping the gateway used to do.

    Returns ``None`` if output_type is ``None`` or unrecognised.
    """
    if output_type is None:
        return None

    if isinstance(output_type, dict):
        return {"type": "json_schema", "schema": output_type}

    if isinstance(output_type, str) and output_type in _PRIMITIVE_JSON_SCHEMAS:
        return {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {"result": _PRIMITIVE_JSON_SCHEMAS[output_type]},
                "required": ["result"],
                "additionalProperties": False,
            },
        }

    logger.warning(
        "Unknown output_type, skipping output_format", output_type=output_type
    )
    return None
