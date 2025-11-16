"""migrate_entities_to_tables

Revision ID: 7694c8910510
Revises: c23dbe59fec6
Create Date: 2025-11-15 09:40:24.819173

"""

from collections.abc import Sequence

import orjson
import sqlalchemy as sa

from alembic import op
from tracecat.identifiers.workflow import WorkspaceUUID

# revision identifiers, used by Alembic.
revision: str = "7694c8910510"
down_revision: str | None = "c23dbe59fec6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Type mapping for table_columns metadata
FIELD_TYPE_TO_SQL_TYPE = {
    "INTEGER": "INTEGER",
    "NUMBER": "NUMERIC",
    "TEXT": "TEXT",
    "BOOL": "BOOLEAN",
    "JSON": "JSONB",
    "DATETIME": "TIMESTAMPTZ",
    "DATE": "DATE",
    "SELECT": "SELECT",
    "MULTI_SELECT": "MULTI_SELECT",
}

# Type mapping for physical table DDL
SQL_TYPE_TO_DDL_TYPE = {
    "INTEGER": "BIGINT",
    "NUMERIC": "NUMERIC",
    "TEXT": "TEXT",
    "BOOLEAN": "BOOLEAN",
    "JSONB": "JSONB",
    "TIMESTAMPTZ": "TIMESTAMPTZ",
    "DATE": "DATE",
    "SELECT": "TEXT",
    "MULTI_SELECT": "JSONB",
}


def sanitize_identifier(identifier: str) -> str:
    """Safely quote PostgreSQL identifiers."""
    return '"' + identifier.replace('"', '""') + '"'


def upgrade() -> None:
    """Migrate entities and records to the tables system.

    This migration:
    1. Creates Table entries for each Entity
    2. Creates TableColumn entries for each EntityField
    3. Creates physical tables in the database schemas
    4. Migrates EntityRecord data to the physical tables
    """
    connection = op.get_bind()

    # Get all entities
    entities_result = connection.execute(
        sa.text("""
            SELECT id, owner_id, key, created_at, updated_at
            FROM entity
            WHERE is_active = true
            ORDER BY created_at
        """)
    )
    entities = entities_result.fetchall()

    for entity in entities:
        entity_id = entity.id
        owner_id = entity.owner_id
        table_name = entity.key  # Use entity key as table name

        # 1. Find or create schema for this workspace
        # Try to find existing schema by checking for tables with same owner
        schema_result = connection.execute(
            sa.text("""
                SELECT DISTINCT table_schema
                FROM information_schema.tables ist
                JOIN tables t ON ist.table_name = t.name
                WHERE t.owner_id = :owner_id
                  AND table_schema LIKE 'tables_%'
                LIMIT 1
            """),
            {"owner_id": owner_id},
        )
        schema_row = schema_result.fetchone()

        if schema_row:
            schema_name = schema_row.table_schema
        else:
            # Create new schema using workspace UUID
            workspace_id = WorkspaceUUID.new(owner_id)
            schema_name = f"tables_{workspace_id.short()}"

            # Create schema (quoted to preserve case)
            schema_identifier = sanitize_identifier(schema_name)
            connection.execute(
                sa.text(f"CREATE SCHEMA IF NOT EXISTS {schema_identifier}")
            )

        # 2. Check if table already exists in this schema - skip if it does
        table_exists = (
            connection.execute(
                sa.text("""
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = :schema AND table_name = :name
                LIMIT 1
            """),
                {"schema": schema_name, "name": table_name},
            ).scalar()
            is not None
        )

        if table_exists:
            continue

        # 3. Create Table entry
        table_insert_result = connection.execute(
            sa.text("""
                INSERT INTO tables (id, owner_id, name, created_at, updated_at, surrogate_id)
                VALUES (gen_random_uuid(), :owner_id, :name, :created_at, :updated_at, DEFAULT)
                RETURNING id
            """),
            {
                "owner_id": owner_id,
                "name": table_name,
                "created_at": entity.created_at,
                "updated_at": entity.updated_at,
            },
        )
        table_id = table_insert_result.fetchone().id

        # 4. Get entity fields and create table columns
        fields_result = connection.execute(
            sa.text("""
                SELECT id, key, type, default_value, created_at, updated_at
                FROM entity_field
                WHERE entity_id = :entity_id AND is_active = true
                ORDER BY created_at
            """),
            {"entity_id": entity_id},
        )
        fields = fields_result.fetchall()

        # Map field types and create columns
        for field in fields:
            field_type = field.type
            sql_type = FIELD_TYPE_TO_SQL_TYPE.get(field_type, "TEXT")

            # Get options for SELECT/MULTI_SELECT fields
            options_data = None
            if field_type in ("SELECT", "MULTI_SELECT"):
                options_result = connection.execute(
                    sa.text("""
                        SELECT key
                        FROM entity_field_option
                        WHERE field_id = :field_id
                        ORDER BY created_at
                    """),
                    {"field_id": field.id},
                )
                options_rows = options_result.fetchall()
                if options_rows:
                    # Format options as array of keys
                    options_data = [opt.key for opt in options_rows]

            connection.execute(
                sa.text("""
                    INSERT INTO table_columns (id, table_id, name, type, nullable, "default", options, created_at, updated_at)
                    VALUES (gen_random_uuid(), :table_id, :name, :type, true, :default_value, CAST(:options AS jsonb), :created_at, :updated_at)
                """),
                {
                    "table_id": table_id,
                    "name": field.key,
                    "type": sql_type,
                    "default_value": field.default_value,
                    "options": orjson.dumps(options_data).decode()
                    if options_data
                    else None,
                    "created_at": field.created_at,
                    "updated_at": field.updated_at,
                },
            )

        # 5. Create physical table
        schema_identifier = sanitize_identifier(schema_name)
        table_identifier = sanitize_identifier(table_name)

        column_defs = []
        for field in fields:
            field_type = field.type
            sql_type = FIELD_TYPE_TO_SQL_TYPE.get(field_type, "TEXT")
            ddl_type = SQL_TYPE_TO_DDL_TYPE.get(sql_type, "TEXT")
            column_name = sanitize_identifier(field.key)
            column_defs.append(f"{column_name} {ddl_type}")

        columns_sql = ", ".join(column_defs)
        create_table_sql = f"""
            CREATE TABLE {schema_identifier}.{table_identifier} (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                {", " + columns_sql if columns_sql else ""}
            )
        """
        connection.execute(sa.text(create_table_sql))

        # 6. Migrate entity records to table rows
        records_result = connection.execute(
            sa.text("""
                SELECT id, data, created_at, updated_at
                FROM entity_record
                WHERE entity_id = :entity_id
                ORDER BY created_at
            """),
            {"entity_id": entity_id},
        )
        records = records_result.fetchall()

        for record in records:
            # Extract data for each field from the JSONB data column
            record_data = record.data

            # Build insert statement (handle entities with no fields)
            if fields:
                field_names = [sanitize_identifier(field.key) for field in fields]
                insert_sql = f"""
                    INSERT INTO {schema_identifier}.{table_identifier}
                    (id, created_at, updated_at, {", ".join(field_names)})
                    VALUES (:id, :created_at, :updated_at, {", ".join(f":field_{i}" for i in range(len(fields)))})
                """
            else:
                # Entity has no fields - insert only base columns
                insert_sql = f"""
                    INSERT INTO {schema_identifier}.{table_identifier}
                    (id, created_at, updated_at)
                    VALUES (:id, :created_at, :updated_at)
                """

            # Prepare values dict
            values = {
                "id": record.id,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            }

            # Add field values if entity has fields
            if fields:
                for i, field in enumerate(fields):
                    field_value = record_data.get(field.key)
                    if (
                        field.type in ("MULTI_SELECT", "JSON")
                        and field_value is not None
                    ):
                        field_value = orjson.dumps(field_value).decode()
                    values[f"field_{i}"] = field_value

            connection.execute(sa.text(insert_sql), values)


def downgrade() -> None:
    """Revert tables back to entities."""
    # Not implementing downgrade for this complex migration
    pass
