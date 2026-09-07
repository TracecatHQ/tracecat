from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any, Protocol

from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.type_api import TypeEngine

from tracecat.query.filters import FilterOp, FilterScalar


class FieldKind(StrEnum):
    """Value families understood by the shared filter compiler."""

    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    TEMPORAL = "temporal"
    ENUM = "enum"
    UUID = "uuid"
    TAG = "tag"


type NormalizedFilterScalar = FilterScalar | Enum
type NormalizedFilterValue = NormalizedFilterScalar | list[NormalizedFilterScalar]
type PredicateFactory = Callable[
    [FilterOp, NormalizedFilterValue | None], ColumnElement[bool]
]
type FilterExpression = (
    ColumnElement[Any] | InstrumentedAttribute[Any] | PredicateFactory
)
type AggregationExpression = ColumnElement[Any] | InstrumentedAttribute[Any]


@dataclass(frozen=True, slots=True)
class ResolvedField:
    """A safe field expression and the operations it supports.

    ``expr`` may be a direct SQL expression or a factory for fields whose
    predicate needs a correlated EXISTS query. Factories that accept non-null
    values must provide ``value_type`` so the compiler can normalize those
    values exactly as it does for direct expressions.
    """

    expr: FilterExpression
    kind: FieldKind
    allowed_ops: frozenset[FilterOp]
    value_type: TypeEngine[Any] | None = None


@dataclass(frozen=True, slots=True)
class ResolvedAggregationField:
    """A trusted expression available to the aggregation compiler.

    Entity-specific resolvers validate whether a field is groupable or can be
    used as an aggregate target before constructing this value. The shared
    compiler uses ``kind`` and the SQL expression type for the generic type
    matrix, bucketing, and output casts.
    """

    expr: AggregationExpression
    kind: FieldKind
    is_multi_valued: bool = False


class FieldResolver(Protocol):
    """Resolve user-facing field addresses to trusted SQL expressions."""

    def resolve(self, field: str) -> ResolvedField | None:
        """Return the resolved field, or ``None`` when it is unknown."""
        ...
