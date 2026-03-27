"""Lightweight output format helpers for the Claude SDK runtime.

This module has NO pydantic-ai dependencies and can be safely imported
in sandboxed runtimes with minimal import footprint.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from tracecat.logger import logger

# JSON Schema primitive type names (used by extract_json_schema).
_JSON_SCHEMA_TYPES = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)


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


def extract_json_schema(
    schema_or_format: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract the raw JSON Schema from provider-specific format payloads."""
    if not isinstance(schema_or_format, dict):
        return None

    # Gemini-style response schema payloads.
    if isinstance(response_schema := schema_or_format.get("response_schema"), dict):
        return response_schema

    # OpenAI response_format payloads with an inner json_schema bundle.
    if isinstance(
        json_schema := schema_or_format.get("json_schema"), dict
    ) and isinstance(
        schema := json_schema.get("schema"),
        dict,
    ):
        return schema

    # Claude SDK / Anthropic-style output_format objects.
    if schema_or_format.get("type") == "json_schema" and isinstance(
        schema := schema_or_format.get("schema"), dict
    ):
        return schema

    # LiteLLM-style schema bundles.
    if "type" not in schema_or_format and isinstance(
        schema := schema_or_format.get("schema"),
        dict,
    ):
        return schema

    if (
        isinstance(schema_type := schema_or_format.get("type"), str)
        and schema_type in _JSON_SCHEMA_TYPES
    ):
        return schema_or_format

    return None


def _schema_from_output_type(output_type: dict[str, Any]) -> dict[str, Any]:
    return extract_json_schema(output_type) or output_type


def build_sdk_output_format(
    output_type: str | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Convert Tracecat's output_type into Claude SDK's output_format shape.

    The Claude SDK expects ``output_format`` as::

        {"type": "json_schema", "schema": <json_schema_dict>}

    For dict output_type, we accept either:
    - a raw JSON Schema object
    - a LiteLLM-compatible schema bundle like
      ``{"name": "...", "schema": {...}, "strict": true}``
    - an already-wrapped ``{"type": "json_schema", "schema": {...}}`` object

    In all dict cases we pass only the inner JSON Schema to the Claude SDK.
    For primitive strings ("int", "str", etc.), we wrap in a
    ``{"type": "object", "properties": {"result": <primitive>}, ...}``
    envelope — same wrapping the gateway used to do.

    Returns ``None`` if output_type is ``None`` or unrecognised.
    """
    if output_type is None:
        return None

    if isinstance(output_type, dict):
        return {"type": "json_schema", "schema": _schema_from_output_type(output_type)}

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
