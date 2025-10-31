"""Utilities for working with Pydantic models.

This module provides utilities for generating zero/default values for Pydantic models
based on their type annotations and field metadata.
"""

from __future__ import annotations

import datetime as dt
import inspect
import math
import types
import uuid
from collections.abc import Sequence
from enum import Enum
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

_ZERO_SENTINEL = object()


def _metadata_lookup(field_info, attr: str):
    """Look up metadata attribute from field info."""
    if field_info is None:
        return None
    for meta in getattr(field_info, "metadata", ()) or ():
        value = getattr(meta, attr, None)
        if value is not None:
            return value
    return None


def _zero_value_for_numeric(field_info, *, is_float: bool) -> int | float:
    """Generate a zero value for numeric types respecting constraints.

    Args:
        field_info: Pydantic field info containing metadata
        is_float: Whether the type is float (True) or int (False)

    Returns:
        A valid zero value respecting gt/ge constraints
    """
    base: float = 0.0
    gt = _metadata_lookup(field_info, "gt")
    ge = _metadata_lookup(field_info, "ge")

    if gt is not None:
        base = float(gt)
        if is_float:
            increment = max(abs(base) * 1e-3, 1e-3)
            base += increment
        else:
            base = math.floor(base) + 1
    elif ge is not None:
        base = float(ge)

    if not is_float:
        return int(math.ceil(base))
    return float(base)


def _strip_annotated(annotation: Any) -> Any:
    """Strip Annotated wrapper to get the underlying type."""
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def _zero_value_for_annotation(annotation: Any, field_info, *, seen: set[type]) -> Any:
    """Generate a zero/default value for a type annotation.

    This function recursively generates appropriate default values for various
    Python types, including primitives, collections, unions, and Pydantic models.

    Args:
        annotation: Type annotation to generate value for
        field_info: Pydantic field info containing metadata
        seen: Set of already processed types to prevent infinite recursion

    Returns:
        An appropriate zero/default value, or _ZERO_SENTINEL if unable to generate
    """
    annotation = _strip_annotated(annotation)
    origin = get_origin(annotation)

    if origin in {Union, types.UnionType}:
        args = get_args(annotation)
        for arg in args:
            if arg is type(None):
                continue
            value = _zero_value_for_annotation(arg, field_info, seen=seen)
            if value is not _ZERO_SENTINEL:
                return value
        if any(arg is type(None) for arg in args):
            return None
        return _ZERO_SENTINEL

    if origin is Literal:
        for arg in get_args(annotation):
            if arg is not None:
                return arg
        return None

    if origin in {list, Sequence}:
        item_type = get_args(annotation)[0] if get_args(annotation) else Any
        min_len = _metadata_lookup(field_info, "min_length")
        length = int(min_len) if min_len else 0
        if length <= 0:
            return []
        item_zero = _zero_value_for_annotation(item_type, None, seen=seen)
        if item_zero is _ZERO_SENTINEL:
            item_zero = ""
        return [item_zero for _ in range(length)]

    if origin in {set, frozenset}:
        item_type = get_args(annotation)[0] if get_args(annotation) else Any
        min_len = _metadata_lookup(field_info, "min_length")
        length = int(min_len) if min_len else 0
        if length <= 0:
            return []
        item_zero = _zero_value_for_annotation(item_type, None, seen=seen)
        if item_zero is _ZERO_SENTINEL:
            item_zero = ""
        return [item_zero for _ in range(length)]

    if origin is tuple:
        args = get_args(annotation)
        if len(args) == 2 and args[1] is Ellipsis:
            item_zero = _zero_value_for_annotation(args[0], None, seen=seen)
            if item_zero is _ZERO_SENTINEL:
                item_zero = ""
            min_len = _metadata_lookup(field_info, "min_length")
            length = int(min_len) if min_len else 0
            return tuple(item_zero for _ in range(length))
        tuple_values: list[Any] = []
        for arg in args:
            item_zero = _zero_value_for_annotation(arg, None, seen=seen)
            if item_zero is _ZERO_SENTINEL:
                item_zero = ""
            tuple_values.append(item_zero)
        return tuple(tuple_values)

    if origin is dict:
        return {}

    if origin is Annotated:
        return _zero_value_for_annotation(
            get_args(annotation)[0], field_info, seen=seen
        )

    if origin is not None:
        if inspect.isclass(origin):
            if issubclass(origin, dict):
                return {}
            if issubclass(origin, Sequence):
                return []
        return _ZERO_SENTINEL

    if annotation in (Any, object):
        return ""
    if annotation is type(None):
        return None
    if annotation is bool:
        return False
    if annotation is str:
        min_length = _metadata_lookup(field_info, "min_length")
        if min_length:
            return "x" * int(min_length)
        return ""
    if annotation is int:
        return _zero_value_for_numeric(field_info, is_float=False)
    if annotation is float:
        return _zero_value_for_numeric(field_info, is_float=True)
    if annotation in {list, Sequence}:
        return []
    if annotation in {set, frozenset}:
        return []
    if annotation is tuple:
        return ()
    if annotation is dict:
        return {}
    if annotation is bytes:
        return ""
    if annotation is uuid.UUID:
        return str(uuid.UUID(int=0))
    if annotation is dt.datetime:
        return dt.datetime(1970, 1, 1, tzinfo=dt.UTC).isoformat()
    if annotation is dt.date:
        return dt.date(1970, 1, 1).isoformat()
    if annotation is dt.time:
        return dt.time(0, 0, 0).isoformat()
    if annotation is dt.timedelta:
        return 0
    if inspect.isclass(annotation):
        if issubclass(annotation, Enum):
            member = next(iter(annotation))
            return getattr(member, "value", member.name)
        if issubclass(annotation, BaseModel):
            if annotation in seen:
                return {}
            annotation.model_rebuild()
            seen.add(annotation)
            try:
                values: dict[str, Any] = {}
                for name, sub_field in annotation.model_fields.items():
                    has_default = sub_field.default is not PydanticUndefined or getattr(
                        sub_field, "default_factory", None
                    )
                    if has_default:
                        continue
                    sub_value = _zero_value_for_annotation(
                        sub_field.annotation, sub_field, seen=seen
                    )
                    if sub_value is _ZERO_SENTINEL:
                        sub_value = ""
                    values[name] = sub_value
                return values
            finally:
                seen.remove(annotation)
    return _ZERO_SENTINEL


def generate_zero_defaults(model_cls: type[BaseModel]) -> dict[str, Any]:
    """Generate zero/default values for all required fields in a Pydantic model.

    This function inspects a Pydantic model and generates appropriate default values
    for all fields that don't have defaults. The generated values respect field
    constraints like min_length, gt, ge, etc.

    Args:
        model_cls: Pydantic model class to generate defaults for

    Returns:
        Dictionary mapping field names to their generated default values

    Example:
        >>> from pydantic import BaseModel, Field
        >>> class User(BaseModel):
        ...     name: str
        ...     age: int = Field(gt=0)
        ...     email: str | None = None
        >>> generate_zero_defaults(User)
        {'name': '', 'age': 1}
    """
    model_cls.model_rebuild()
    defaults: dict[str, Any] = {}
    seen: set[type] = set()
    for name, field in model_cls.model_fields.items():
        has_default = field.default is not PydanticUndefined or getattr(
            field, "default_factory", None
        )
        if has_default:
            continue
        value = _zero_value_for_annotation(field.annotation, field, seen=seen)
        if value is _ZERO_SENTINEL:
            value = ""
        defaults[name] = value
    return defaults
