from collections.abc import Sequence
from typing import assert_never

import sqlalchemy as sa

from tracecat.db.models import TableColumn
from tracecat.exceptions import TracecatValidationError
from tracecat.query.filters import FilterOp
from tracecat.query.resolver import FieldKind, ResolvedField
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


class TableFieldResolver:
    """Resolve user-facing table column names to trusted SQL expressions."""

    def __init__(self, columns: Sequence[TableColumn]) -> None:
        self._columns_by_name = {column.name: column for column in columns}

    def resolve(self, field: str) -> ResolvedField | None:
        """Return the resolved table field, or ``None`` when it is unknown."""
        if is_internal_column_name(field):
            return None

        exact_column = self._columns_by_name.get(field)
        if field in _SYSTEM_TEMPORAL_COLUMNS:
            return self._resolve_system_column(field)
        if field in _UNRESOLVABLE_SYSTEM_COLUMNS:
            return None

        try:
            normalized_name = validate_identifier(field)
        except ValueError:
            if exact_column is None:
                return None
            return self._resolve_user_column(exact_column)

        if normalized_name in _SYSTEM_TEMPORAL_COLUMNS:
            return self._resolve_system_column(normalized_name)
        if normalized_name in _UNRESOLVABLE_SYSTEM_COLUMNS:
            return None

        column = exact_column or self._columns_by_name.get(normalized_name)
        if column is None:
            return None
        return self._resolve_user_column(column)

    @staticmethod
    def _resolve_system_column(name: str) -> ResolvedField:
        return ResolvedField(
            expr=sa.column(name, sa.TIMESTAMP(timezone=True)),
            kind=FieldKind.TEMPORAL,
            allowed_ops=TEMPORAL_OPS,
        )

    @staticmethod
    def _resolve_user_column(column: TableColumn) -> ResolvedField:
        try:
            sql_type = SqlType(column.type)
        except ValueError as exc:
            raise TracecatValidationError(
                f"Column {column.name!r} has invalid stored type {column.type!r}"
            ) from exc

        match sql_type:
            case SqlType.TEXT:
                kind = FieldKind.TEXT
                allowed_ops = TEXT_OPS
            case SqlType.INTEGER | SqlType.NUMERIC:
                kind = FieldKind.NUMBER
                allowed_ops = NUMBER_OPS
            case SqlType.BOOLEAN:
                kind = FieldKind.BOOLEAN
                allowed_ops = BOOLEAN_OPS
            case SqlType.DATE | SqlType.TIMESTAMPTZ:
                kind = FieldKind.TEMPORAL
                allowed_ops = TEMPORAL_OPS
            case SqlType.SELECT:
                kind = FieldKind.ENUM
                allowed_ops = ENUM_OPS
            case SqlType.JSONB | SqlType.MULTI_SELECT:
                raise TracecatValidationError(
                    f"Column {column.name!r} of type {sql_type.value} "
                    "cannot be used in filters"
                )
            case _ as unreachable:
                assert_never(unreachable)

        physical_name = sanitize_identifier(column.name)
        return ResolvedField(
            expr=sa.column(physical_name, sa_type_for_column(sql_type)),
            kind=kind,
            allowed_ops=allowed_ops,
        )
