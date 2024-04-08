from __future__ import annotations

import inspect
from types import GenericAlias, UnionType
from typing import Any, Literal, get_origin

from pydantic import BaseModel


class _DefaultType(BaseModel):
    type: str
    value: Any


class _ParameterType(BaseModel):
    type: str
    args: list[_ParameterType] | None = None
    value: str | int | float | None = None


class ParameterSpec(BaseModel):
    name: str
    type: _ParameterType
    default: _DefaultType | None = None


class IntegrationSpec(BaseModel):
    name: str
    description: str  # Possibly redundant
    docstring: str
    platform: str  # e.g. AWS, Google Workspace, Crowdstrike
    parameters: list[ParameterSpec]


def _parse_annotation(annotation: type | UnionType) -> dict:
    """Recursively parse type annotations into a JSON-serializable schema."""
    # Literal type
    if get_origin(annotation) is Literal:
        # NOTE: Parsing literals with complex type is undefined behavior
        print("LITERAL_ENUM")
        return {
            "type": "enum",
            "args": [
                {
                    "type": type(a).__name__,
                    "value": a,
                }
                for a in annotation.__args__
            ],
        }
    # Complex annotation
    if hasattr(annotation, "__args__"):
        return {
            "type": _get_complex_type_name(annotation),
            "args": [_parse_annotation(arg) for arg in annotation.__args__],
        }
    return {"type": annotation.__name__}


def _get_complex_type_name(
    annotation: type,
) -> Literal["list", "dict", "union", "tuple", "enum"]:
    """Get the name of a type annotation."""
    if isinstance(annotation, GenericAlias):
        return annotation.__name__
    if isinstance(annotation, UnionType):
        return "union"
    if get_origin(annotation) is Literal:
        return "literal_enum"
    raise ValueError(f"Unsupported type: {annotation}")


def _get_default_type(default: Any) -> dict | None:
    if default == inspect.Parameter.empty:
        # No default value
        return None
    return {"type": type(default).__name__, "value": default}


def param_to_spec(name: str, param: inspect.Parameter) -> ParameterSpec:
    return ParameterSpec(
        name=name,
        type=_parse_annotation(param.annotation),
        default=_get_default_type(param.default),
    )
