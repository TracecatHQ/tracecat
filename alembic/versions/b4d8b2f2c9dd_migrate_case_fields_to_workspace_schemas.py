"""Migrate case fields data into workspace-specific schemas."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.interfaces import ReflectedColumn

from alembic import op
from tracecat.identifiers.workflow import WorkspaceUUID

revision: str = "b4d8b2f2c9dd"
down_revision: str | None = "a6c2d9e7f5b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constants for schema and table management
PUBLIC_SCHEMA = "public"
SCHEMA_PREFIX = "custom_fields_"
TABLE_NAME = "case_fields"
# Base columns that exist in all case_fields tables (system columns)
BASE_COLUMNS = {"id", "case_id", "created_at", "updated_at", "owner_id"}
# Columns that should not be copied to workspace schemas (reserved system columns)
RESERVED_COLUMNS = {"id", "case_id", "created_at", "updated_at"}


def _workspace_schema(workspace_id: uuid.UUID) -> str:
    """
    Generate a workspace-specific schema name from a workspace UUID.

    Args:
        workspace_id: The UUID of the workspace

    Returns:
        Schema name in format: {SCHEMA_PREFIX}{short_workspace_id}
    """
    ws_short = WorkspaceUUID.new(workspace_id).short()
    return f"{SCHEMA_PREFIX}{ws_short}"


def _prepare_identifier(name: str, bind: sa.engine.Connection) -> str:
    """
    Properly quote SQL identifiers to prevent injection and handle special characters.

    Args:
        name: The identifier name to quote
        bind: Database connection for dialect-specific quoting

    Returns:
        Properly quoted identifier safe for SQL execution
    """
    preparer = sa.sql.compiler.IdentifierPreparer(bind.dialect)
    return preparer.quote(name)


def _ensure_workspace_table(
    bind: sa.engine.Connection,
    schema_name: str,
    dynamic_columns: list[ReflectedColumn],
) -> None:
    """
    Ensure the workspace-scoped case_fields table exists and carries all dynamic columns.

    If the table already exists (for example, created by runtime code before this migration),
    we add any missing dynamic columns so that the bulk INSERT later will succeed.
    """
    preparer = sa.sql.compiler.IdentifierPreparer(bind.dialect)
    schema_quoted = preparer.quote_schema(schema_name)
    table_quoted = preparer.quote(TABLE_NAME)

    # Always ensure the schema exists first.
    op.execute(sa.DDL(f"CREATE SCHEMA IF NOT EXISTS {schema_quoted}"))

    inspector = sa.inspect(bind)
    # Create the base table (with dynamic columns) if it's not already present.
    metadata = sa.MetaData()
    workspace_table = sa.Table(
        TABLE_NAME,
        metadata,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        *(
            sa.Column(  # Dynamic custom columns
                column["name"],
                column["type"],
                nullable=column.get("nullable", True),
            )
            for column in dynamic_columns
        ),
        schema=schema_name,
    )
    workspace_table.create(bind=bind, checkfirst=True)

    # Ensure the FK constraint is present exactly once.
    fk_name = f"fk_{TABLE_NAME}_case"
    existing_fks = {
        fk["name"] for fk in inspector.get_foreign_keys(TABLE_NAME, schema=schema_name)
    }
    if fk_name not in existing_fks:
        op.execute(
            sa.DDL(
                f"""
                ALTER TABLE {schema_quoted}.{table_quoted}
                ADD CONSTRAINT {preparer.quote(fk_name)}
                FOREIGN KEY (case_id) REFERENCES {PUBLIC_SCHEMA}.cases(id)
                ON DELETE CASCADE
                """
            )
        )

    # Refresh column metadata (table may have been created above).
    existing_columns = {
        column["name"]
        for column in inspector.get_columns(TABLE_NAME, schema=schema_name)
    }

    # Add any missing dynamic columns that might exist in the shared table.
    for column in dynamic_columns:
        column_name = column["name"]
        if column_name in existing_columns:
            continue

        column_identifier = _prepare_identifier(column_name, bind)
        column_type = column["type"]
        type_sql = bind.dialect.type_compiler.process(column_type)

        op.execute(
            sa.text(
                f"""
                ALTER TABLE {schema_quoted}.{table_quoted}
                ADD COLUMN {column_identifier} {type_sql}
                """
            )
        )


def upgrade() -> None:
    """
    Migrate case fields data from the shared public table to workspace-specific schemas.

    Migration process:
    1. Inspect the current public case_fields table structure
    2. Identify custom columns (beyond base system columns)
    3. For each workspace:
       - Create a workspace-specific schema and table
       - Copy all relevant data from public table to workspace table
       - Verify data integrity with row count checks
    """
    bind = op.get_bind()
    assert bind is not None

    inspector = sa.inspect(bind)

    # Get the structure of the current public case_fields table
    public_columns = inspector.get_columns(TABLE_NAME, schema=PUBLIC_SCHEMA)

    # Identify custom columns that need to be recreated in each workspace table
    # Everything beyond the base columns is a user-defined custom field
    dynamic_columns = [
        column for column in public_columns if column["name"] not in BASE_COLUMNS
    ]

    # Get all workspace IDs to know which workspace schemas to create
    # Owner IDs on the public table tell us which workspace each row belongs to
    workspace_ids = [
        row[0]
        for row in bind.execute(
            sa.text(f"SELECT id FROM {PUBLIC_SCHEMA}.workspace")
        ).fetchall()
    ]

    # Prepare column list for data copying (exclude owner_id since it's not needed in workspace tables)
    column_order = [
        column["name"] for column in public_columns if column["name"] != "owner_id"
    ]
    column_select = ", ".join(_prepare_identifier(col, bind) for col in column_order)

    # Process each workspace individually
    for raw_workspace_id in workspace_ids:
        # Ensure workspace_id is a proper UUID object
        workspace_uuid = (
            raw_workspace_id
            if isinstance(raw_workspace_id, uuid.UUID)
            else uuid.UUID(str(raw_workspace_id))
        )
        schema_name = _workspace_schema(workspace_uuid)

        # Ensure the workspace-specific table exists and contains every custom column
        _ensure_workspace_table(bind, schema_name, dynamic_columns)

        # Prepare identifiers for the data copy operation
        schema_quoted = _prepare_identifier(schema_name, bind)
        table_quoted = f"{schema_quoted}.{_prepare_identifier(TABLE_NAME, bind)}"

        # Copy all data belonging to this workspace from public table to workspace table
        insert_sql = sa.text(
            f"""
            INSERT INTO {table_quoted} ({column_select})
            SELECT {column_select}
            FROM {PUBLIC_SCHEMA}.{TABLE_NAME}
            WHERE owner_id = :workspace_id
            """
        )
        bind.execute(insert_sql, {"workspace_id": workspace_uuid})

        # Verify data integrity by comparing row counts
        source_count = bind.execute(
            sa.text(
                f"""
                SELECT COUNT(*) FROM {PUBLIC_SCHEMA}.{TABLE_NAME}
                WHERE owner_id = :workspace_id
                """
            ),
            {"workspace_id": workspace_uuid},
        ).scalar_one()

        dest_count = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM {table_quoted}")
        ).scalar_one()

        # Ensure all data was copied successfully
        if source_count != dest_count:
            raise RuntimeError(
                f"Row count mismatch for workspace {workspace_uuid}: "
                f"{source_count=} vs {dest_count=}"
            )


def downgrade() -> None:
    """
    Rollback the migration by merging workspace-specific data back into the shared public table.

    Rollback process:
    1. For each workspace schema:
       - Copy data back to the public table with proper owner_id
       - Handle conflicts with upsert logic
       - Drop the workspace schema after successful copy
    2. Restore the original shared table structure
    """
    bind = op.get_bind()
    assert bind is not None

    inspector = sa.inspect(bind)

    # Get all workspace IDs to know which schemas to process during rollback
    workspace_ids = [
        row[0]
        for row in bind.execute(
            sa.text(f"SELECT id FROM {PUBLIC_SCHEMA}.workspace")
        ).fetchall()
    ]

    # Process each workspace schema for rollback
    for raw_workspace_id in workspace_ids:
        # Ensure workspace_id is a proper UUID object
        workspace_uuid = (
            raw_workspace_id
            if isinstance(raw_workspace_id, uuid.UUID)
            else uuid.UUID(str(raw_workspace_id))
        )
        schema_name = _workspace_schema(workspace_uuid)

        # Skip if the workspace schema doesn't exist
        if not inspector.has_schema(schema_name):
            continue

        # If schema exists but table doesn't, just clean up the empty schema
        if not inspector.has_table(TABLE_NAME, schema=schema_name):
            op.execute(sa.DDL(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
            continue

        # Get columns from the WORKSPACE schema table (not public) to avoid column mismatch
        # The workspace table may have different custom fields than the public table
        ws_columns = inspector.get_columns(TABLE_NAME, schema=schema_name)
        column_order = [col["name"] for col in ws_columns]
        column_list = ", ".join(_prepare_identifier(col, bind) for col in column_order)

        # Prepare UPDATE assignments for conflict resolution (upsert logic)
        update_assignments = ", ".join(
            f"{_prepare_identifier(col, bind)} = EXCLUDED.{_prepare_identifier(col, bind)}"
            for col in column_order
        )
        update_assignments = (
            f"{update_assignments}, owner_id = EXCLUDED.owner_id"
            if update_assignments
            else "owner_id = EXCLUDED.owner_id"
        )

        # Prepare identifiers for the merge operation
        schema_quoted = _prepare_identifier(schema_name, bind)
        table_quoted = f"{schema_quoted}.{_prepare_identifier(TABLE_NAME, bind)}"

        # Copy data back to public table with upsert logic to handle conflicts
        # This restores the owner_id column and merges workspace data back
        insert_sql = sa.text(
            f"""
            INSERT INTO {PUBLIC_SCHEMA}.{TABLE_NAME} ({column_list}, owner_id)
            SELECT {column_list}, :workspace_id
            FROM {table_quoted}
            ON CONFLICT (id) DO UPDATE
            SET {update_assignments}
            """
        )
        bind.execute(insert_sql, {"workspace_id": workspace_uuid})

        # Clean up the workspace schema after successful data migration
        op.execute(sa.DDL(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
