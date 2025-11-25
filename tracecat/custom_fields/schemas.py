from __future__ import annotations

from typing import Self

import sqlalchemy as sa

from tracecat.core.schemas import Schema
from tracecat.tables.common import parse_postgres_default
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableColumnUpdate


class CustomFieldRead(Schema):
    """Read model for a custom field."""

    id: str
    type: SqlType
    description: str
    nullable: bool
    default: str | None
    reserved: bool

    @classmethod
    def from_sa(
        cls,
        column: sa.engine.interfaces.ReflectedColumn,
        *,
        reserved_fields: set[str] | None = None,
    ) -> Self:
        raw_type = column["type"]
        if isinstance(raw_type, SqlType):
            sql_type = raw_type
        else:
            if isinstance(raw_type, str):
                type_str = raw_type.upper()
            else:
                type_str = str(raw_type).upper()
                if hasattr(raw_type, "timezone"):
                    type_str = (
                        "TIMESTAMP WITH TIME ZONE"
                        if getattr(raw_type, "timezone", False)
                        else "TIMESTAMP WITHOUT TIME ZONE"
                    )
            if type_str == "BIGINT":
                type_str = SqlType.INTEGER
            if type_str == "TIMESTAMP WITH TIME ZONE":
                sql_type = SqlType.TIMESTAMPTZ
            elif type_str in {"TIMESTAMP WITHOUT TIME ZONE", "TIMESTAMP"}:
                sql_type = SqlType.TIMESTAMP
            else:
                sql_type = SqlType(type_str)

        reserved_set = reserved_fields or set()

        return cls(
            id=column["name"],
            type=sql_type,
            description=column.get("comment") or "",
            nullable=column["nullable"],
            default=parse_postgres_default(column.get("default")),
            reserved=column["name"] in reserved_set,
        )


class CustomFieldCreate(TableColumnCreate):
    """Create a new custom field."""


class CustomFieldUpdate(TableColumnUpdate):
    """Update a custom field."""


__all__ = [
    "CustomFieldRead",
    "CustomFieldCreate",
    "CustomFieldUpdate",
]
