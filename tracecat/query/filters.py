from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    model_validator,
)

MAX_FILTER_DEPTH = 4
MAX_FILTER_CONDITIONS = 50
MAX_FILTER_VALUES = 1_000


class FilterOp(StrEnum):
    """Operations supported by the shared filter language."""

    EQ = "eq"
    NE = "ne"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    IS_NULL = "is_null"


type FilterScalar = str | int | float | bool | Decimal | datetime | date | UUID
type FilterValue = FilterScalar | list[FilterScalar]


class _FilterModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    @model_validator(mode="after")
    def validate_tree_limits(self) -> Self:
        if isinstance(self, Condition | AndClause | OrClause | NotClause):
            _validate_tree_limits(self)
        return self


class Condition(_FilterModel):
    """A predicate over one resolver-defined field."""

    field: str = Field(min_length=1)
    op: FilterOp
    value: FilterValue | None = Field(default=None)


class AndClause(_FilterModel):
    """A conjunction of one or more filters."""

    and_: list[Filter] = Field(
        alias="and",
        serialization_alias="and",
        min_length=1,
    )


class OrClause(_FilterModel):
    """A disjunction of one or more filters."""

    or_: list[Filter] = Field(
        alias="or",
        serialization_alias="or",
        min_length=1,
    )


class NotClause(_FilterModel):
    """The negation of one filter."""

    not_: Filter = Field(alias="not", serialization_alias="not")


def _filter_discriminator(value: object) -> str:
    if isinstance(value, Condition):
        return "condition"
    if isinstance(value, AndClause):
        return "and"
    if isinstance(value, OrClause):
        return "or"
    if isinstance(value, NotClause):
        return "not"
    if not isinstance(value, Mapping):
        return "invalid"

    node_types: list[str] = []
    if "field" in value:
        node_types.append("condition")
    if "and" in value or "and_" in value:
        node_types.append("and")
    if "or" in value or "or_" in value:
        node_types.append("or")
    if "not" in value or "not_" in value:
        node_types.append("not")
    return node_types[0] if len(node_types) == 1 else "invalid"


type Filter = Annotated[
    Annotated[Condition, Tag("condition")]
    | Annotated[AndClause, Tag("and")]
    | Annotated[OrClause, Tag("or")]
    | Annotated[NotClause, Tag("not")],
    Discriminator(_filter_discriminator),
]


def _tree_stats(node: Filter, *, depth: int = 1) -> tuple[int, int, int]:
    match node:
        case Condition(value=value):
            value_count = len(value) if isinstance(value, list) else value is not None
            return depth, 1, int(value_count)
        case AndClause(and_=children) | OrClause(or_=children):
            stats = [_tree_stats(child, depth=depth + 1) for child in children]
        case NotClause(not_=child):
            stats = [_tree_stats(child, depth=depth + 1)]

    return (
        max(node_depth for node_depth, _, _ in stats),
        sum(condition_count for _, condition_count, _ in stats),
        sum(value_count for _, _, value_count in stats),
    )


def _validate_tree_limits(node: Filter) -> None:
    depth, condition_count, value_count = _tree_stats(node)
    if depth > MAX_FILTER_DEPTH:
        raise ValueError(f"Filter tree exceeds maximum depth {MAX_FILTER_DEPTH}")
    if condition_count > MAX_FILTER_CONDITIONS:
        raise ValueError(
            f"Filter tree exceeds maximum condition count {MAX_FILTER_CONDITIONS}"
        )
    if value_count > MAX_FILTER_VALUES:
        raise ValueError(f"Filter tree exceeds maximum value count {MAX_FILTER_VALUES}")


AndClause.model_rebuild(_types_namespace={"Filter": Filter})
OrClause.model_rebuild(_types_namespace={"Filter": Filter})
NotClause.model_rebuild(_types_namespace={"Filter": Filter})
