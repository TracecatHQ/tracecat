from __future__ import annotations

import inspect
from types import GenericAlias, UnionType
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

from tracecat.integrations.utils import FunctionType


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
        return "enum"
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


def validate_type_constraints(func: FunctionType):
    """Validate type constraints for registered integrations."""

    sig = inspect.signature(func)

    for _param_name, param in sig.parameters.items():
        # Skip *args and **kwargs
        if param.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue

        annotation = param.annotation

        # Skip parameters without annotations or return value annotation
        if annotation is inspect.Parameter.empty:
            continue

        origin = get_origin(annotation)
        args = get_args(annotation)

        # if origin is a list type, ensure that the inner type is a single built-in type
        if origin in (list, tuple, UnionType):
            # Count the number of built-in types in the inner type
            n_builtins = sum(_is_builtin_primitive_type(arg) for arg in args)
            # If there's more than 1 builtin type and the other isn't None
            if n_builtins != 1 and type(None) not in args:
                raise ValueError(
                    f"Error when registering {func.__name__!r}. {origin.__name__!r} type {annotation} must have exactly"
                    " one inner builtin type, or be an optional builtin type."
                )
            #
        # Dictinoaries are input as json objects, so this can be arbitrarily complex (though not recommended)
        # We'll only support one level deep for now
        elif origin is dict:
            # Ensure that the inner type is a single built-in type
            K, V = args
            if K is not str:
                raise ValueError(
                    f"Error when registering {func.__name__!r}. {origin.__name__!r} type {annotation} must have a string key type."
                )
            if not (_is_builtin_primitive_type(V) or V is Any):
                raise ValueError(
                    f"Error when registering {func.__name__!r}. {origin.__name__!r} type {annotation} must have a single built-in inner type."
                )


__ALLOWED_BUILTIN_TYPES__ = {int, float, str, bool}


def _is_builtin_primitive_type(annotation: type) -> bool:
    """Check if a type annotation is a builtin type."""
    return annotation in __ALLOWED_BUILTIN_TYPES__
