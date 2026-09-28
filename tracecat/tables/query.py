from collections.abc import Sequence
from typing import Literal, assert_never

import sqlalchemy as sa

from tracecat.db.models import TableColumn
from tracecat.exceptions import TracecatValidationError
from tracecat.query.filters import FilterOp
from tracecat.query.resolver import (
    FieldKind,
    ResolvedAggregationField,
    ResolvedField,
)
from tracecat.tables.common import (
    is_internal_column_name,
    sa_type_for_column,
    sanitize_identifier,
    validate_identifier,
)
from tracecat.tables.enums import SqlType

TEXT_OPS = frozenset(
    {
        FilterOp.EQ,
        FilterOp.NE,
        FilterOp.IN,
        FilterOp.NOT_IN,
        FilterOp.CONTAINS,
        FilterOp.STARTS_WITH,
        FilterOp.IS_NULL,
    }
)
NUMBER_OPS = frozenset(
    {
        FilterOp.EQ,
        FilterOp.NE,
        FilterOp.IN,
        FilterOp.NOT_IN,
        FilterOp.GT,
        FilterOp.GTE,
        FilterOp.LT,
        FilterOp.LTE,
        FilterOp.IS_NULL,
    }
)
BOOLEAN_OPS = frozenset({FilterOp.EQ, FilterOp.NE, FilterOp.IS_NULL})
TEMPORAL_OPS = frozenset(
    {
        FilterOp.EQ,
        FilterOp.NE,
        FilterOp.GT,
        FilterOp.GTE,
        FilterOp.LT,
        FilterOp.LTE,
        FilterOp.IS_NULL,
    }
)
ENUM_OPS = frozenset(
    {
        FilterOp.EQ,
        FilterOp.NE,
        FilterOp.IN,
        FilterOp.NOT_IN,
        FilterOp.IS_NULL,
    }
)

_SYSTEM_TEMPORAL_COLUMNS = frozenset({"created_at", "updated_at"})
_UNRESOLVABLE_SYSTEM_COLUMNS = frozenset({"id"})
type FieldUsage = Literal["filters", "aggregations"]


class TableFieldResolver:
    """Resolve user-facing table column names to trusted SQL expressions."""

    def __init__(self, columns: Sequence[TableColumn]) -> None:
        self._columns_by_name = {column.name: column for column in columns}

    def resolve(self, field: str) -> ResolvedField | None:
        """Return the resolved table field, or ``None`` when it is unknown."""
        resolved = self._resolve_typed_column(field, usage="filters")
        if resolved is None:
            return None
        expression, kind = resolved
        return ResolvedField(
            expr=expression,
            kind=kind,
            allowed_ops=self._allowed_filter_ops(kind),
        )

    def resolve_aggregation(self, field: str) -> ResolvedAggregationField | None:
        """Return a trusted, single-valued table aggregation field."""
        resolved = self._resolve_typed_column(field, usage="aggregations")
        if resolved is None:
            return None
        expression, kind = resolved
        return ResolvedAggregationField(
            expr=expression,
            kind=kind,
            is_multi_valued=False,
        )

    def _resolve_typed_column(
        self,
        field: str,
        *,
        usage: FieldUsage,
    ) -> tuple[sa.ColumnClause, FieldKind] | None:
        resolved = self._resolve_column(field)
        if resolved is None:
            return None
        name, column = resolved
        if column is None:
            return sa.column(name, sa.TIMESTAMP(timezone=True)), FieldKind.TEMPORAL

        sql_type = self._sql_type(column)
        kind = self._field_kind(column, sql_type, usage=usage)
        physical_name = sanitize_identifier(column.name)
        return sa.column(physical_name, sa_type_for_column(sql_type)), kind

    def _resolve_column(self, field: str) -> tuple[str, TableColumn | None] | None:
        if is_internal_column_name(field):
            return None

        exact_column = self._columns_by_name.get(field)
        if field in _SYSTEM_TEMPORAL_COLUMNS:
            return field, None
        if field in _UNRESOLVABLE_SYSTEM_COLUMNS:
            return None

        try:
            normalized_name = validate_identifier(field)
        except ValueError:
            if exact_column is None:
                return None
            return exact_column.name, exact_column

        if normalized_name in _SYSTEM_TEMPORAL_COLUMNS:
            return normalized_name, None
        if normalized_name in _UNRESOLVABLE_SYSTEM_COLUMNS:
            return None

        column = exact_column or self._columns_by_name.get(normalized_name)
        if column is None:
            return None
        return column.name, column

    @staticmethod
    def _sql_type(column: TableColumn) -> SqlType:
        try:
            return SqlType(column.type)
        except ValueError as exc:
            raise TracecatValidationError(
                f"Column {column.name!r} has invalid stored type {column.type!r}"
            ) from exc

    @staticmethod
    def _field_kind(
        column: TableColumn,
        sql_type: SqlType,
        *,
        usage: FieldUsage,
    ) -> FieldKind:
        match sql_type:
            case SqlType.TEXT:
                return FieldKind.TEXT
            case SqlType.INTEGER | SqlType.NUMERIC:
                return FieldKind.NUMBER
            case SqlType.BOOLEAN:
                return FieldKind.BOOLEAN
            case SqlType.DATE | SqlType.TIMESTAMPTZ:
                return FieldKind.TEMPORAL
            case SqlType.SELECT:
                return FieldKind.ENUM
            case SqlType.JSONB | SqlType.MULTI_SELECT:
                raise TracecatValidationError(
                    f"Column {column.name!r} of type {sql_type.value} "
                    f"cannot be used in {usage}"
                )
            case _ as unreachable:
                assert_never(unreachable)

    @staticmethod
    def _allowed_filter_ops(kind: FieldKind) -> frozenset[FilterOp]:
        match kind:
            case FieldKind.TEXT:
                return TEXT_OPS
            case FieldKind.NUMBER:
                return NUMBER_OPS
            case FieldKind.BOOLEAN:
                return BOOLEAN_OPS
            case FieldKind.TEMPORAL:
                return TEMPORAL_OPS
            case FieldKind.ENUM:
                return ENUM_OPS
            case FieldKind.UUID | FieldKind.TAG:
                raise AssertionError(f"Unsupported table field kind: {kind}")
