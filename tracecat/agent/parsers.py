from __future__ import annotations

from typing import Any

import orjson
from pydantic import Json
from pydantic_ai import StructuredDict
from pydantic_core import from_json


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
