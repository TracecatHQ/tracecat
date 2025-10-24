"""Remove dynamic custom columns from public.case_fields metadata table."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.interfaces import ReflectedColumn
from tracecat.identifiers.workflow import WorkspaceUUID

# Alembic migration metadata
revision: str = "c13c1c2f4d93"
down_revision: str | None = "b4d8b2f2c9dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constants for the migration
PUBLIC_SCHEMA = "public"
TABLE_NAME = "case_fields"
# These are the core columns that should always exist in the case_fields table
BASE_COLUMNS = {"id", "case_id", "created_at", "updated_at", "owner_id"}


def _workspace_schema(workspace_id: uuid.UUID | str) -> str:
    """
    Generate the workspace-specific schema name for case_fields table.

    Each workspace has its own schema with a shortened UUID identifier.
    This function converts a workspace UUID into the corresponding schema name.

    Args:
        workspace_id: The workspace UUID (as UUID object or string)

    Returns:
        Schema name in format "case_fields_{short_uuid}"
    """
    workspace_uuid = (
        workspace_id
        if isinstance(workspace_id, uuid.UUID)
        else uuid.UUID(str(workspace_id))
    )
    ws_short = WorkspaceUUID.new(workspace_uuid).short()
    return f"case_fields_{ws_short}"


def upgrade() -> None:
    """
    Migration upgrade: Remove dynamic custom columns from public.case_fields table.

    This migration cleans up the public.case_fields metadata table by removing
    any dynamically added custom columns that were created beyond the base schema.
    It also removes any indexes that reference these dynamic columns to avoid
    constraint violations.

    The process:
    1. Identify all columns that aren't part of the base schema
    2. Find and drop any indexes that reference these dynamic columns
    3. Drop the dynamic columns themselves
    """
    bind = op.get_bind()
    assert bind is not None

    inspector = sa.inspect(bind)

    # Get all indexes on the table before we start dropping columns
    indexes = inspector.get_indexes(TABLE_NAME, schema=PUBLIC_SCHEMA)
    dynamic_index_names: list[str] = []
    dynamic_columns: list[str] = []

    # Identify all columns that are not part of the base schema
    for column in inspector.get_columns(TABLE_NAME, schema=PUBLIC_SCHEMA):
        column_name = column["name"]
        if column_name not in BASE_COLUMNS:
            dynamic_columns.append(column_name)

    # If no dynamic columns exist, nothing to clean up
    if not dynamic_columns:
        return

    dynamic_column_set = set(dynamic_columns)

    # Find indexes that reference any of the dynamic columns we're about to drop
    for index in indexes:
        column_names = index.get("column_names") or []
        # If this index references any dynamic columns, mark it for deletion
        if dynamic_column_set.intersection(column_names) and (
            name := index.get("name")
        ):
            dynamic_index_names.append(name)

    # Drop indexes first to avoid foreign key constraint violations
    for index_name in dynamic_index_names:
        op.drop_index(
            index_name,
            table_name=TABLE_NAME,
            schema=PUBLIC_SCHEMA,
        )

    # Now safely drop the dynamic columns
    for column_name in dynamic_columns:
        op.drop_column(TABLE_NAME, column_name, schema=PUBLIC_SCHEMA)


def downgrade() -> None:
    """
    Migration downgrade: Restore dynamic custom columns to public.case_fields table.

    This reverses the upgrade by examining all workspace-specific case_fields tables
    and recreating any custom columns that exist in those tables but are missing
    from the public metadata table.

    The process:
    1. Find all workspace IDs in the system
    2. For each workspace, examine its case_fields table schema
    3. Collect all non-base columns from all workspace tables
    4. Add any missing columns back to the public.case_fields table

    Note: This only restores the column structure, not the data or indexes.
    """
    bind = op.get_bind()
    assert bind is not None

    inspector = sa.inspect(bind)

    # Dictionary to store unique column definitions found across all workspaces
    column_definitions: dict[str, ReflectedColumn] = {}

    # Get all workspace IDs from the workspace table
    workspace_ids = [
        row[0]
        for row in bind.execute(
            sa.text(f"SELECT id FROM {PUBLIC_SCHEMA}.workspace")
        ).fetchall()
    ]

    # Examine each workspace's case_fields table to find custom columns
    for raw_workspace_id in workspace_ids:
        schema_name = _workspace_schema(raw_workspace_id)

        # Skip if this workspace doesn't have a case_fields table
        if not inspector.has_table(TABLE_NAME, schema=schema_name):
            continue

        # Collect all non-base columns from this workspace's table
        for column in inspector.get_columns(TABLE_NAME, schema=schema_name):
            column_name = column["name"]
            if column_name in BASE_COLUMNS:
                continue
            # Store the column definition (first occurrence wins if there are conflicts)
            column_definitions.setdefault(column_name, column)

    # Get current columns in the public table to avoid duplicates
    existing_columns = {
        column["name"]
        for column in inspector.get_columns(TABLE_NAME, schema=PUBLIC_SCHEMA)
    }

    # Add back any missing custom columns to the public table
    for column_name, column in column_definitions.items():
        if column_name in existing_columns:
            continue
        column_type = column["type"]
        nullable = column.get("nullable", True)
        op.add_column(
            TABLE_NAME,
            sa.Column(column_name, column_type, nullable=nullable),
            schema=PUBLIC_SCHEMA,
        )
        existing_columns.add(column_name)
