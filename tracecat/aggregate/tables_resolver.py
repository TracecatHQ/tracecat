"""Tables field resolver for aggregate queries."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import JSONB

from tracecat.aggregate.schemas import AggFn, FilterOp
from tracecat.db.models import Table
from tracecat.tables.enums import SqlType
from tracecat.tables.service import sanitize_identifier

_SYSTEM_COLUMNS: dict[str, str] = {
    "id": "text",
    "created_at": "timestamptz",
    "updated_at": "timestamptz",
}

_NUMERIC_SQL_TYPES = frozenset({SqlType.INTEGER, SqlType.NUMERIC})

_SEARCHABLE_SQL_TYPES = frozenset(
    {
        SqlType.TEXT,
        SqlType.JSONB,
        SqlType.SELECT,
        SqlType.MULTI_SELECT,
    }
)

_JSONB_STORED_TYPES = frozenset({SqlType.JSONB, SqlType.MULTI_SELECT})


def _op_clause(
    col: sa.ColumnElement[Any], op: FilterOp, value: Any
) -> sa.ColumnElement[bool]:
    """Apply a standard filter operator to a column."""
    match op:
        case FilterOp.EQ:
            return col == value
        case FilterOp.NE:
            return col != value
        case FilterOp.IN:
            return col.in_(value)
        case FilterOp.NOT_IN:
            return ~col.in_(value)
        case FilterOp.GT:
            return col > value
        case FilterOp.GTE:
            return col >= value
        case FilterOp.LT:
            return col < value
        case FilterOp.LTE:
            return col <= value
        case FilterOp.IS_NULL:
            return col.is_(None)
        case FilterOp.IS_NOT_NULL:
            return col.isnot(None)
        case FilterOp.ICONTAINS:
            return col.ilike(func.concat("%", value, "%"))
        case _:
            raise ValueError(f"Unsupported operator {op!r}")


class TablesFieldResolver:
    """Field resolver for dynamic Table aggregate queries."""

    def __init__(self, table: Table, schema_name: str) -> None:
        self.table = table
        self.schema_name = schema_name
        self._column_types: dict[str, SqlType] = {
            col.name: SqlType(col.type) for col in table.columns
        }

    def resolve_field(self, field_name: str) -> sa.ColumnElement[Any]:
        """Map a field name to a SQLAlchemy column clause."""
        if field_name in _SYSTEM_COLUMNS:
            return sa.column(field_name)
        if field_name in self._column_types:
            return sa.column(sanitize_identifier(field_name))
        raise ValueError(f"Unknown table column: {field_name!r}")

    def resolve_filter(
        self,
        field_name: str,
        op: FilterOp,
        value: Any,
    ) -> sa.ColumnElement[bool]:
        """Build a WHERE clause for a filter condition."""
        col = self.resolve_field(field_name)

        # MULTI_SELECT: has_any / has_all via JSONB operators
        if field_name in self._column_types:
            col_type = self._column_types[field_name]
            if col_type == SqlType.MULTI_SELECT:
                if op == FilterOp.HAS_ANY:
                    return col.op("?|")(sa.cast(value, sa.ARRAY(sa.TEXT)))
                if op == FilterOp.HAS_ALL:
                    return col.op("@>")(
                        sa.cast(
                            sa.func.cast(sa.literal(value), sa.TEXT),
                            JSONB,
                        )
                    )
            # JSONB icontains: cast to text first
            if col_type in _JSONB_STORED_TYPES and op == FilterOp.ICONTAINS:
                return sa.cast(col, sa.TEXT).ilike(func.concat("%", value, "%"))

        return _op_clause(col, op, value)

    def validate_agg(self, func: AggFn, field_name: str | None) -> None:
        """Validate aggregate function compatibility with column type."""
        if field_name is None:
            return  # count(*) always valid

        if field_name in _SYSTEM_COLUMNS:
            if func in (AggFn.SUM, AggFn.MEAN):
                raise ValueError(
                    f"{func.value} is not supported for system column {field_name!r}"
                )
            return

        if field_name not in self._column_types:
            raise ValueError(f"Unknown table column: {field_name!r}")

        col_type = self._column_types[field_name]
        if func in (AggFn.SUM, AggFn.MEAN) and col_type not in _NUMERIC_SQL_TYPES:
            raise ValueError(
                f"{func.value} is not supported for column {field_name!r} "
                f"(type {col_type.value}, requires INTEGER or NUMERIC)"
            )

    def get_search_clause(self, search_term: str) -> sa.ColumnElement[bool] | None:
        """Return ILIKE search across text-searchable columns."""
        if len(search_term) > 1000:
            raise ValueError("Search term cannot exceed 1000 characters")
        if "\x00" in search_term:
            raise ValueError("Search term cannot contain null bytes")

        searchable = [
            col.name
            for col in self.table.columns
            if SqlType(col.type) in _SEARCHABLE_SQL_TYPES
        ]
        if not searchable:
            return None

        search_pattern = sa.func.concat("%", search_term, "%")
        conditions: list[sa.ColumnElement[bool]] = []
        for col_name in searchable:
            sanitized = sanitize_identifier(col_name)
            col_type = self._column_types[col_name]
            if col_type in _JSONB_STORED_TYPES:
                conditions.append(
                    sa.cast(sa.column(sanitized), sa.TEXT).ilike(search_pattern)
                )
            else:
                conditions.append(sa.column(sanitized).ilike(search_pattern))

        return sa.or_(*conditions)

    def get_base_from(self) -> sa.FromClause:
        """Return the dynamic table as FROM clause."""
        return sa.table(sanitize_identifier(self.table.name), schema=self.schema_name)

    def get_mandatory_filters(self) -> list[sa.ColumnElement[bool]]:
        """No extra filters needed — RLS handles workspace scoping."""
        return []

    def get_field_type(self, field_name: str) -> str:
        """Return the type name for a field."""
        if field_name in _SYSTEM_COLUMNS:
            return _SYSTEM_COLUMNS[field_name]
        if field_name in self._column_types:
            col_type = self._column_types[field_name]
            match col_type:
                case SqlType.TEXT | SqlType.SELECT:
                    return "text"
                case SqlType.INTEGER:
                    return "integer"
                case SqlType.NUMERIC:
                    return "number"
                case SqlType.BOOLEAN:
                    return "boolean"
                case SqlType.DATE:
                    return "date"
                case SqlType.TIMESTAMPTZ:
                    return "timestamptz"
                case SqlType.JSONB | SqlType.MULTI_SELECT:
                    return "jsonb"
                case _:
                    return "text"
        raise ValueError(f"Unknown table column: {field_name!r}")
