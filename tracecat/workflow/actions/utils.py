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
    """Generate a zero-ish value for numeric types respecting constraints.

    Args:
        field_info: Pydantic field info containing metadata
        is_float: Whether the type is float (True) or int (False)

    Returns:
        A value that stays within the gt/ge/lt/le window when possible.
    """

    gt = _metadata_lookup(field_info, "gt")
    ge = _metadata_lookup(field_info, "ge")
    lt = _metadata_lookup(field_info, "lt")
    le = _metadata_lookup(field_info, "le")

    if is_float:
        def _nudge(value: float, *, direction: Literal["up", "down"]) -> float:
            """Move one representable float away from a boundary."""
            if math.isinf(value) or math.isnan(value):
                return value
            target = math.inf if direction == "up" else -math.inf
            stepped = math.nextafter(float(value), target)
            if stepped == value:
                delta = max(abs(value) * 1e-6, 1e-6)
                return value + delta if direction == "up" else value - delta
            return stepped

        lower_value: float | None = None
        lower_strict = False

        def _apply_lower(value: float, *, strict: bool) -> None:
            nonlocal lower_value, lower_strict
            if lower_value is None:
                lower_value = value
                lower_strict = strict
                return
            if value > lower_value:
                lower_value = value
                lower_strict = strict
                return
            if value == lower_value and strict and not lower_strict:
                lower_strict = True

        if ge is not None:
            _apply_lower(float(ge), strict=False)
        if gt is not None:
            _apply_lower(float(gt), strict=True)

        upper_value: float | None = None
        upper_strict = False

        def _apply_upper(value: float, *, strict: bool) -> None:
            nonlocal upper_value, upper_strict
            if upper_value is None:
                upper_value = value
                upper_strict = strict
                return
            if value < upper_value:
                upper_value = value
                upper_strict = strict
                return
            if value == upper_value and strict and not upper_strict:
                upper_strict = True

        if le is not None:
            _apply_upper(float(le), strict=False)
        if lt is not None:
            _apply_upper(float(lt), strict=True)

        lower_bound = (
            -math.inf
            if lower_value is None
            else lower_value if not lower_strict else _nudge(lower_value, direction="up")
        )
        upper_bound = (
            math.inf
            if upper_value is None
            else upper_value if not upper_strict else _nudge(upper_value, direction="down")
        )

        if lower_bound > upper_bound:
            # Constraints are incompatible; fall back to the tightest lower bound.
            return float(lower_bound)

        candidate = 0.0
        if candidate < lower_bound:
            candidate = lower_bound
        if candidate > upper_bound:
            candidate = upper_bound
        return float(candidate)

    lower_bound: int | None = None
    if ge is not None:
        bound = math.ceil(float(ge))
        lower_bound = bound if lower_bound is None else max(lower_bound, bound)
    if gt is not None:
        bound = math.floor(float(gt)) + 1
        lower_bound = bound if lower_bound is None else max(lower_bound, bound)

    upper_bound: int | None = None
    if le is not None:
        bound = math.floor(float(le))
        upper_bound = bound if upper_bound is None else min(upper_bound, bound)
    if lt is not None:
        bound = math.ceil(float(lt)) - 1
        upper_bound = bound if upper_bound is None else min(upper_bound, bound)

    if (
        lower_bound is not None
        and upper_bound is not None
        and lower_bound > upper_bound
    ):
        return lower_bound

    candidate = 0
    if lower_bound is not None and candidate < lower_bound:
        candidate = lower_bound
    if upper_bound is not None and candidate > upper_bound:
        candidate = upper_bound

    return int(candidate)


def _strip_annotated(annotation: Any) -> Any:
    """Strip Annotated wrapper to get the underlying type."""
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def _generate_unique_set_placeholders(
    item_annotation: Any,
    base_value: Any,
    length: int,
    *,
    seen: set[type],
) -> list[Any] | None:
    """Generate distinct placeholder values for set-like annotations.

    Tries to respect the element type so that Pydantic coercion keeps the
    resulting set at the required minimum length.
    """

    if length <= 0:
        return []

    annotation = _strip_annotated(item_annotation)
    origin = get_origin(annotation)

    if origin in {Union, types.UnionType}:
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if not args:
            return [None] * length
        for arg in args:
            candidate_base = base_value
            if candidate_base in {None, _ZERO_SENTINEL}:
                candidate_base = _zero_value_for_annotation(arg, None, seen=seen)
                if candidate_base is _ZERO_SENTINEL:
                    candidate_base = ""
            placeholders = _generate_unique_set_placeholders(
                arg, candidate_base, length, seen=seen
            )
            if placeholders is not None:
                return placeholders
        return None

    if origin is Literal:
        literal_values: list[Any] = []
        for value in get_args(annotation):
            if value is None:
                continue
            if value not in literal_values:
                literal_values.append(value)
            if len(literal_values) == length:
                return literal_values
        return None

    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        enum_values: list[Any] = []
        for member in annotation:
            enum_value = getattr(member, "value", member.name)
            if enum_value not in enum_values:
                enum_values.append(enum_value)
            if len(enum_values) == length:
                return enum_values
        return None

    if annotation is bool:
        if length <= 2:
            return [False, True][:length]
        return None

    if annotation is int:
        start = int(base_value) if isinstance(base_value, int) else 0
        return [start + idx for idx in range(length)]

    if annotation is float:
        start = float(base_value) if isinstance(base_value, (int, float)) else 0.0
        increment = 1.0 if start == 0.0 else max(abs(start) * 0.1, 1.0)
        return [start + idx * increment for idx in range(length)]

    if annotation is uuid.UUID:
        return [str(uuid.UUID(int=idx)) for idx in range(length)]

    if annotation is dt.date:
        base_date = (
            dt.date.fromisoformat(base_value)
            if isinstance(base_value, str) and base_value
            else dt.date(1970, 1, 1)
        )
        return [
            (base_date + dt.timedelta(days=idx)).isoformat() for idx in range(length)
        ]

    if annotation is dt.datetime:
        base_dt = (
            dt.datetime.fromisoformat(base_value)
            if isinstance(base_value, str) and base_value
            else dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
        )
        return [
            (base_dt + dt.timedelta(seconds=idx)).isoformat()
            for idx in range(length)
        ]

    if annotation is dt.time:
        base_time = (
            dt.time.fromisoformat(base_value)
            if isinstance(base_value, str) and base_value
            else dt.time(0, 0, 0)
        )
        base_dt = dt.datetime.combine(dt.date(1970, 1, 1), base_time)
        return [
            (base_dt + dt.timedelta(seconds=idx)).time().isoformat()
            for idx in range(length)
        ]

    if annotation is dt.timedelta:
        start = int(base_value) if isinstance(base_value, (int, float)) else 0
        return [start + idx for idx in range(length)]

    if annotation is bytes:
        seed = base_value if isinstance(base_value, bytes) else b""
        token = seed or b"value"
        values: list[bytes] = []
        for idx in range(length):
            if idx == 0:
                values.append(seed or token + b"_0")
            else:
                values.append(token + f"_{idx}".encode())
        return values

    if annotation in {Any, object}:
        return [f"value_{idx}" for idx in range(length)]

    if isinstance(base_value, str):
        seed = base_value or "value"
        values: list[str] = []
        for idx in range(length):
            if idx == 0:
                values.append(base_value)
            else:
                values.append(f"{seed}_{idx}")
        return values

    if isinstance(base_value, (int, float)):
        start = float(base_value)
        return [start + idx for idx in range(length)]

    if isinstance(base_value, bytes):
        seed = base_value or b"value"
        return [seed + f"_{idx}".encode() for idx in range(length)]

    return None


def _fallback_set_variant(base_value: Any, index: int) -> Any:
    """Generate a best-effort variant when unique placeholders cannot be derived."""

    if index == 0:
        return base_value

    if isinstance(base_value, str):
        seed = base_value or "value"
        return f"{seed}_{index}"

    if isinstance(base_value, bytes):
        seed = base_value or b"value"
        return seed + f"_{index}".encode()

    if isinstance(base_value, bool):
        return bool(index % 2)

    if isinstance(base_value, int):
        return base_value + index

    if isinstance(base_value, float):
        return base_value + index

    return (base_value, index)


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
        placeholders = _generate_unique_set_placeholders(
            item_type, item_zero, length, seen=seen
        )
        if placeholders is None:
            stripped_item = _strip_annotated(item_type)
            item_origin = get_origin(stripped_item)
            if item_origin is Literal:
                literal_values = [
                    value
                    for value in get_args(stripped_item)
                    if value is not None
                ]
                if literal_values:
                    multiplied = (
                        literal_values * (length // len(literal_values) + 1)
                    )[:length]
                    return multiplied
            if stripped_item is bool:
                placeholders = [False, True][:length]
                if len(placeholders) < length:
                    placeholders.extend([True] * (length - len(placeholders)))
                return placeholders
            placeholders = [
                _fallback_set_variant(item_zero, idx) for idx in range(length)
            ]
            return placeholders
        return placeholders

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
