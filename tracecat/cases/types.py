"""Typed contracts for resolving case query fields."""

from dataclasses import dataclass

from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import FromClause

from tracecat.cases.enums import CaseFieldKind
from tracecat.query.resolver import ResolvedAggregationField
from tracecat.tables.enums import SqlType


@dataclass(frozen=True, slots=True)
class CaseFieldJoinSpec:
    """A join required to use a case field in an aggregation."""

    key: str
    target: FromClause
    onclause: ColumnElement[bool]
    is_outer: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedCaseAggregationField:
    """A case aggregation field and its optional backing-table join."""

    field: ResolvedAggregationField
    join: CaseFieldJoinSpec | None = None


@dataclass(frozen=True, slots=True)
class CustomFieldDefinition:
    """Validated custom-field metadata used to construct SQL expressions."""

    name: str
    physical_name: str
    sql_type: SqlType
    kind: CaseFieldKind | None
