from __future__ import annotations

from typing import Any, Self

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
    options: list[str] | None = None
    always_visible: bool = False

    @classmethod
    def from_sa(
        cls,
        column: sa.engine.interfaces.ReflectedColumn,
        *,
        reserved_fields: set[str] | None = None,
        field_schema: dict[str, Any] | None = None,
    ) -> Self:
        raw_type = column["type"]
        sql_type: SqlType
        if isinstance(raw_type, SqlType):
            sql_type = raw_type
        else:
            type_str: str
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
                sql_type = SqlType.INTEGER
            elif type_str == "TIMESTAMP WITH TIME ZONE":
                sql_type = SqlType.TIMESTAMPTZ
            elif type_str in {"TIMESTAMP WITHOUT TIME ZONE", "TIMESTAMP"}:
                sql_type = SqlType.TIMESTAMP
            else:
                sql_type = SqlType(type_str)

        reserved_set = reserved_fields or set()
        options: list[str] | None = None

        schema_metadata = field_schema.get(column["name"]) if field_schema else None
        if schema_metadata and "type" in schema_metadata:
            sql_type = SqlType(schema_metadata["type"])
            options = schema_metadata.get("options")
        always_visible = (
            bool(schema_metadata.get("always_visible")) if schema_metadata else False
        )

        return cls(
            id=column["name"],
            type=sql_type,
            description=column.get("comment") or "",
            nullable=column["nullable"],
            default=parse_postgres_default(column.get("default")),
            reserved=column["name"] in reserved_set,
            options=options,
            always_visible=always_visible,
        )


class CustomFieldCreate(TableColumnCreate):
    """Create a new custom field."""

    always_visible: bool = False


class CustomFieldUpdate(TableColumnUpdate):
    """Update a custom field."""

    always_visible: bool | None = None


__all__ = [
    "CustomFieldRead",
    "CustomFieldCreate",
    "CustomFieldUpdate",
]
