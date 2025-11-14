"""migrate_entity_to_table_data

Revision ID: 6a3220db1064
Revises: 3da6f9f95dda
Create Date: 2025-11-13 12:21:57.700030

This migration handles the data migration from Entity/EntityRecord to Table/TableRow.
It migrates all active entities and their records to the new table system.

"""

import re
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text

from alembic import op
from tracecat.identifiers.workflow import WorkspaceUUID

# revision identifiers, used by Alembic.
revision: str = "6a3220db1064"
down_revision: str | None = "3da6f9f95dda"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Type mappings
FIELD_TYPE_TO_SQL_TYPE = {
    "INTEGER": "INTEGER",
    "NUMBER": "NUMERIC",
    "TEXT": "TEXT",
    "BOOL": "BOOLEAN",
    "JSON": "JSONB",
    "DATETIME": "TIMESTAMPTZ",
    "DATE": "TIMESTAMP",
    "SELECT": "ENUM",
    "MULTI_SELECT": "JSONB",
}


def sanitize_identifier(name: str) -> str:
    """Sanitize an identifier to be safe for use as a table/column name."""
    # Remove or replace invalid characters
    sanitized = re.sub(r"[^\w]", "_", name)
    # Ensure it starts with a letter or underscore
    if sanitized and not sanitized[0].isalpha() and sanitized[0] != "_":
        sanitized = "_" + sanitized
    # Ensure it's not empty
    if not sanitized:
        sanitized = "_unnamed"
    # Convert to lowercase and limit length
    return sanitized.lower()[:63]


def upgrade() -> None:
    """Migrate entities and entity records to tables and table rows."""
    bind = op.get_bind()

    # Check if there are any active entities to migrate
    result = bind.execute(text("SELECT COUNT(*) FROM entity WHERE is_active = true"))
    entity_count = result.scalar()

    if entity_count == 0:
        print("No entities to migrate.")
        return

    print(f"Migrating {entity_count} entities to tables...")

    # Load all active entities
    entities_result = bind.execute(
        text("""
            SELECT id, owner_id, key, created_at, updated_at
            FROM entity
            WHERE is_active = true
            ORDER BY created_at
        """)
    )
    entities = list(entities_result.fetchall())

    for entity in entities:
        entity_id, owner_id, entity_key, created_at, updated_at = entity

        try:
            print(f"  Migrating entity '{entity_key}' ({entity_id})...")

            # Get workspace schema name (tables_ws_{workspace_short_id})
            workspace_short = WorkspaceUUID.new(owner_id).short()
            schema_name = f"tables_{workspace_short}"

            # Sanitize table name and handle conflicts
            base_table_name = sanitize_identifier(entity_key)
            table_name = base_table_name
            suffix = 1

            while True:
                check_result = bind.execute(
                    text("""
                        SELECT COUNT(*) FROM "tables"
                        WHERE owner_id = :owner_id AND name = :name
                    """),
                    {"owner_id": owner_id, "name": table_name},
                )
                if check_result.scalar() == 0:
                    break
                table_name = f"{base_table_name}_{suffix}"
                suffix += 1

            # Create the table record
            table_id = uuid.uuid4()
            bind.execute(
                text("""
                    INSERT INTO "tables" (id, owner_id, name, created_at, updated_at)
                    VALUES (:id, :owner_id, :name, NOW(), NOW())
                """),
                {"id": table_id, "owner_id": owner_id, "name": table_name},
            )

            # Load entity fields
            fields_result = bind.execute(
                text("""
                    SELECT id, key, type, is_active, default_value
                    FROM entityfield
                    WHERE entity_id = :entity_id
                    ORDER BY created_at
                """),
                {"entity_id": entity_id},
            )
            fields = list(fields_result.fetchall())

            # Create table columns payload
            table_columns_payload: list[dict[str, Any]] = []
            for field in fields:
                field_id, field_key, field_type, is_active, default_value = field

                if not is_active:
                    continue

                sql_type = FIELD_TYPE_TO_SQL_TYPE.get(field_type, "TEXT")
                column_name = sanitize_identifier(field_key)

                # Handle enum fields specially
                default_payload = default_value
                if sql_type == "ENUM":
                    # Load enum options
                    options_result = bind.execute(
                        text("""
                            SELECT key FROM entityfieldoption
                            WHERE field_id = :field_id
                            ORDER BY created_at
                        """),
                        {"field_id": field_id},
                    )
                    enum_values = [row[0] for row in options_result.fetchall()]
                    if enum_values:
                        default_payload = {"enum_values": enum_values}
                        if (
                            isinstance(default_value, str)
                            and default_value.strip() in enum_values
                        ):
                            default_payload["default"] = default_value.strip()

                column_entry: dict[str, Any] = {
                    "id": uuid.uuid4(),
                    "name": column_name,
                    "type": sql_type,
                    "nullable": True,
                }
                if default_payload is not None:
                    column_entry["default"] = default_payload
                table_columns_payload.append(column_entry)

            for column in table_columns_payload:
                bind.execute(
                    text(
                        """
                        INSERT INTO table_columns (id, table_id, name, type, nullable, "default")
                        VALUES (:id, :table_id, :name, :type, :nullable, :default)
                        """
                    ),
                    {
                        "id": column["id"],
                        "table_id": table_id,
                        "name": column["name"],
                        "type": column["type"],
                        "nullable": column["nullable"],
                        "default": column.get("default"),
                    },
                )

            # Create the dynamic table schema
            bind.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
            bind.execute(
                text(f"""
                CREATE TABLE IF NOT EXISTS "{schema_name}"."{table_name}" (
                    id UUID PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    data JSONB NOT NULL DEFAULT '{{}}'::jsonb
                )
            """)
            )

            # Migrate entity records
            records_result = bind.execute(
                text("""
                    SELECT id, data, created_at, updated_at
                    FROM entityrecord
                    WHERE entity_id = :entity_id
                    ORDER BY created_at
                """),
                {"entity_id": entity_id},
            )
            records = list(records_result.fetchall())

            if records:
                # Insert records in chunks
                chunk_size = 1000
                for i in range(0, len(records), chunk_size):
                    chunk = records[i : i + chunk_size]
                    for record in chunk:
                        record_id, data, rec_created_at, rec_updated_at = record
                        bind.execute(
                            text(f"""
                            INSERT INTO "{schema_name}"."{table_name}"
                            (id, data, created_at, updated_at)
                            VALUES (:id, :data, :created_at, :updated_at)
                        """),
                            {
                                "id": record_id,
                                "data": data or {},
                                "created_at": rec_created_at,
                                "updated_at": rec_updated_at,
                            },
                        )

            # Migrate case links
            case_links_result = bind.execute(
                text("""
                    SELECT case_id, record_id
                    FROM caserecord
                    WHERE entity_id = :entity_id
                """),
                {"entity_id": entity_id},
            )
            case_links = list(case_links_result.fetchall())

            for case_id, record_id in case_links:
                bind.execute(
                    text("""
                        INSERT INTO case_table_row (id, owner_id, case_id, table_id, row_id, created_at, updated_at)
                        VALUES (:id, :owner_id, :case_id, :table_id, :row_id, NOW(), NOW())
                        ON CONFLICT (case_id, table_id, row_id) DO NOTHING
                    """),
                    {
                        "id": uuid.uuid4(),
                        "owner_id": owner_id,
                        "case_id": case_id,
                        "table_id": table_id,
                        "row_id": record_id,
                    },
                )

            print(
                f"✓ Migrated to table '{table_name}' ({len(records)} records, {len(case_links)} case links)"
            )

        except Exception as exc:
            print(f"✗ Failed to migrate entity '{entity_key}': {exc}")
            # Continue with other entities
            continue

    print("Entity-to-table migration completed.")


def downgrade() -> None:
    """Data migrations cannot be automatically reversed."""
    pass
