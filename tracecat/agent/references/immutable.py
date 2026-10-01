"""Immutable JSON containers with ordinary JSON wire serialization."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated

from pydantic import (
    AfterValidator,
    BeforeValidator,
    JsonValue,
    PlainSerializer,
    TypeAdapter,
)


def _freeze_mapping[V](value: Mapping[str, V]) -> Mapping[str, V]:
    return MappingProxyType(dict(value))


def _json_value(value: "FrozenJSON") -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


type FrozenMap[V] = Annotated[
    Mapping[str, V],
    AfterValidator(_freeze_mapping),
    PlainSerializer(_json_value, return_type=JsonValue),
]
type FrozenJSON = (
    FrozenMap[FrozenJSON] | tuple[FrozenJSON, ...] | str | bool | int | float | None
)


_json_adapter = TypeAdapter(JsonValue)


def _validate_json(value: object) -> JsonValue:
    # Preserve JsonValue admission: tuple/set/bytes coercion is not JSON input.
    return _json_adapter.validate_python(value)


type FrozenJSONObject = Annotated[
    FrozenMap[FrozenJSON], BeforeValidator(_validate_json)
]
