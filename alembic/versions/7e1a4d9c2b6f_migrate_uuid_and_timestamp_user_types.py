"""migrate_uuid_and_timestamp_user_types

Revision ID: 7e1a4d9c2b6f
Revises: bf38f2aa1c77
Create Date: 2026-03-29 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Any

import orjson
import sqlalchemy as sa

from alembic import op
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.tables.common import parse_postgres_default, prepare_default_value
from tracecat.tables.enums import SqlType

# revision identifiers, used by Alembic.
revision: str = "7e1a4d9c2b6f"
down_revision: str | None = "bf38f2aa1c77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOOKUP_SCHEMA_PREFIX = "tables_"
_CASE_FIELD_SCHEMA_PREFIX = "custom_fields_"
_CASE_FIELD_TABLE_NAME = "case_fields"
_TYPE_MAPPING: dict[str, SqlType] = {
    "UUID": SqlType.TEXT,
    "TIMESTAMP": SqlType.TIMESTAMPTZ,
}


def _workspace_schema(prefix: str, workspace_id: Any) -> str:
    return f"{prefix}{WorkspaceUUID.new(workspace_id).short()}"


def _quote_ident(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _qualified_table(schema_name: str, table_name: str) -> str:
    return f"{_quote_ident(schema_name)}.{_quote_ident(table_name)}"


def _render_using_expression(column_name: str, old_type: str) -> str:
    quoted_column = _quote_ident(column_name)
    if old_type == "UUID":
        return f"{quoted_column}::text"
    return f"{quoted_column} AT TIME ZONE 'UTC'"


def _physical_type_name(target_type: SqlType) -> str:
    if target_type is SqlType.TEXT:
        return "text"
    return "timestamptz"


def _lookup_column_default(
    connection: sa.Connection,
    *,
    schema_name: str,
    table_name: str,
    column_name: str,
) -> str | None:
    return connection.execute(
        sa.text(
            """
            SELECT column_default
            FROM information_schema.columns
            WHERE table_schema = :schema_name
              AND table_name = :table_name
              AND column_name = :column_name
            """
        ),
        {
            "schema_name": schema_name,
            "table_name": table_name,
            "column_name": column_name,
        },
    ).scalar_one_or_none()


def _prepare_migrated_default(
    target_type: SqlType,
    raw_default: Any,
) -> tuple[Any | None, str | None]:
    if raw_default is None:
        return None, None
    normalized_default, rendered_default = prepare_default_value(
        target_type, raw_default
    )
    return normalized_default, rendered_default


def _rewrite_column(
    connection: sa.Connection,
    *,
    schema_name: str,
    table_name: str,
    column_name: str,
    old_type: str,
    raw_default: Any,
) -> tuple[Any | None, str]:
    target_type = _TYPE_MAPPING[old_type]
    normalized_default, rendered_default = _prepare_migrated_default(
        target_type, raw_default
    )
    qualified_table = _qualified_table(schema_name, table_name)
    quoted_column = _quote_ident(column_name)

    connection.execute(
        sa.text(
            f"ALTER TABLE {qualified_table} ALTER COLUMN {quoted_column} DROP DEFAULT"
        )
    )
    connection.execute(
        sa.text(
            " ".join(
                [
                    f"ALTER TABLE {qualified_table}",
                    f"ALTER COLUMN {quoted_column}",
                    f"TYPE {_physical_type_name(target_type)}",
                    f"USING {_render_using_expression(column_name, old_type)}",
                ]
            )
        )
    )
    if rendered_default is not None:
        connection.execute(
            sa.text(
                " ".join(
                    [
                        f"ALTER TABLE {qualified_table}",
                        f"ALTER COLUMN {quoted_column}",
                        f"SET DEFAULT {rendered_default}",
                    ]
                )
            )
        )
    return normalized_default, target_type.value


def _migrate_lookup_table_columns(connection: sa.Connection) -> None:
    rows = connection.execute(
        sa.text(
            """
            SELECT
                tc.id,
                tc.name AS column_name,
                tc.type AS old_type,
                tc."default" AS metadata_default,
                t.name AS table_name,
                t.workspace_id
            FROM public.table_column tc
            JOIN public.tables t ON t.id = tc.table_id
            WHERE tc.type IN ('UUID', 'TIMESTAMP')
            ORDER BY t.workspace_id, t.name, tc.name
            """
        )
    ).mappings()

    for row in rows:
        schema_name = _workspace_schema(_LOOKUP_SCHEMA_PREFIX, row["workspace_id"])
        physical_default = _lookup_column_default(
            connection,
            schema_name=schema_name,
            table_name=row["table_name"],
            column_name=row["column_name"],
        )
        raw_default = row["metadata_default"]
        if raw_default is None and physical_default is not None:
            raw_default = parse_postgres_default(physical_default)

        normalized_default, new_type = _rewrite_column(
            connection,
            schema_name=schema_name,
            table_name=row["table_name"],
            column_name=row["column_name"],
            old_type=row["old_type"],
            raw_default=raw_default,
        )
        connection.execute(
            sa.text(
                """
                UPDATE public.table_column
                SET type = :new_type, "default" = CAST(:normalized_default AS JSONB)
                WHERE id = :column_id
                """
            ),
            {
                "new_type": new_type,
                "normalized_default": (
                    orjson.dumps(normalized_default).decode()
                    if normalized_default is not None
                    else None
                ),
                "column_id": row["id"],
            },
        )


def _migrate_case_field_columns(connection: sa.Connection) -> None:
    definitions = connection.execute(
        sa.text(
            """
            SELECT id, workspace_id, schema
            FROM public.case_field
            ORDER BY workspace_id
            """
        )
    ).mappings()

    for definition in definitions:
        schema_data = definition["schema"] or {}
        if not isinstance(schema_data, dict):
            raise ValueError("Case field schema must be a JSON object")

        updated_schema = dict(schema_data)
        migrated_field_names: list[tuple[str, str]] = []
        for field_name, field_def in schema_data.items():
            if not isinstance(field_def, dict):
                raise ValueError(
                    f"Case field schema for {field_name!r} must be an object"
                )
            old_type = field_def.get("type")
            if old_type not in _TYPE_MAPPING:
                continue
            migrated_field_names.append((field_name, old_type))
            updated_field_def = dict(field_def)
            updated_field_def["type"] = _TYPE_MAPPING[old_type].value
            updated_schema[field_name] = updated_field_def

        if not migrated_field_names:
            continue

        schema_name = _workspace_schema(
            _CASE_FIELD_SCHEMA_PREFIX, definition["workspace_id"]
        )
        for field_name, old_type in migrated_field_names:
            physical_default = _lookup_column_default(
                connection,
                schema_name=schema_name,
                table_name=_CASE_FIELD_TABLE_NAME,
                column_name=field_name,
            )
            raw_default = (
                parse_postgres_default(physical_default)
                if physical_default is not None
                else None
            )
            _rewrite_column(
                connection,
                schema_name=schema_name,
                table_name=_CASE_FIELD_TABLE_NAME,
                column_name=field_name,
                old_type=old_type,
                raw_default=raw_default,
            )

        connection.execute(
            sa.text(
                """
                UPDATE public.case_field
                SET schema = CAST(:updated_schema AS JSONB)
                WHERE id = :definition_id
                """
            ),
            {
                "updated_schema": orjson.dumps(updated_schema).decode(),
                "definition_id": definition["id"],
            },
        )


def upgrade() -> None:
    """Migrate legacy UUID and TIMESTAMP user-defined columns to supported types."""
    connection = op.get_bind()
    _migrate_lookup_table_columns(connection)
    _migrate_case_field_columns(connection)


def downgrade() -> None:
    """Downgrade not supported after UUID/TIMESTAMP migration.

    UUID columns are rewritten to TEXT and TIMESTAMP columns are rewritten to
    TIMESTAMPTZ with UTC semantics. The original user-defined type information
    is intentionally discarded, so automatic downgrade cannot safely restore the
    previous schema.
    """
    raise NotImplementedError(
        "Downgrade not supported for this migration. "
        "UUID columns were converted to TEXT and TIMESTAMP columns were "
        "converted to TIMESTAMPTZ with UTC semantics. Manual rollback required."
    )
