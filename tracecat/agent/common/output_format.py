"""Shared output schema normalization and Claude SDK formatting.

This module is safe to import in sandboxed runtimes with a minimal import
footprint.
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

    # Schema bundles (name + schema + strict).
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
    extracted = extract_json_schema(output_type)
    return extracted if extracted is not None else output_type


def build_output_schema(
    output_type: str | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Normalize Tracecat's output type into a provider-independent JSON Schema.

    Accepts raw schemas, provider format wrappers, and primitive type aliases.
    Primitive aliases use the standard object envelope with a ``result`` field.
    Returns ``None`` for absent or unrecognized output types.
    """
    if output_type is None:
        return None

    if isinstance(output_type, dict):
        return _schema_from_output_type(output_type)

    if output_type in _PRIMITIVE_JSON_SCHEMAS:
        return {
            "type": "object",
            "properties": {"result": _PRIMITIVE_JSON_SCHEMAS[output_type]},
            "required": ["result"],
            "additionalProperties": False,
        }

    logger.warning(
        "Unknown output_type, skipping output_format", output_type=output_type
    )
    return None


def build_sdk_output_format(
    output_type: str | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Wrap Tracecat's output schema in Claude SDK's output_format shape."""
    schema = build_output_schema(output_type)
    if schema is None:
        return None
    return {"type": "json_schema", "schema": schema}
