from __future__ import annotations

from typing import Any

import orjson
from pydantic import Json
from pydantic_ai import StructuredDict
from pydantic_core import from_json

from tracecat.logger import logger


def try_parse_json(x: Any) -> Json[Any] | str:
    if not isinstance(x, str | bytes | bytearray):
        return x

    try:
        return orjson.loads(x)
    except orjson.JSONDecodeError:
        try:
            return from_json(x, allow_partial=True)
        except ValueError:
            return x


SUPPORTED_OUTPUT_TYPES: dict[str, type[Any]] = {
    "bool": bool,
    "float": float,
    "int": int,
    "str": str,
    "list[bool]": list[bool],
    "list[float]": list[float],
    "list[int]": list[int],
    "list[str]": list[str],
}


def parse_output_type(output_type: str | dict[str, Any] | None) -> type[Any]:
    """Normalize an OutputType spec into a concrete Python type."""
    if output_type is None:
        return str

    if isinstance(output_type, str):
        try:
            return SUPPORTED_OUTPUT_TYPES[output_type]
        except KeyError as e:
            raise ValueError(
                f"Unknown output type: {output_type}. "
                f"Expected one of: {', '.join(SUPPORTED_OUTPUT_TYPES.keys())}"
            ) from e

    if isinstance(output_type, dict):
        schema_name = output_type.get("name") or output_type.get("title")
        schema_description = output_type.get("description")
        return StructuredDict(
            output_type, name=schema_name, description=schema_description
        )

    return str


# JSON Schema mappings for primitive output types
_PRIMITIVE_JSON_SCHEMAS: dict[str, dict[str, Any]] = {
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
