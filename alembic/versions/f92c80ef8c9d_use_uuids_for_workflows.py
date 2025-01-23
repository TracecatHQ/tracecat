"""Use UUIDs for workflows

Revision ID: f92c80ef8c9d
Revises: db3c91261770
Create Date: 2025-01-22 00:34:44.053138

"""

import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.logger import logger

# revision identifiers, used by Alembic.
revision: str = "f92c80ef8c9d"
down_revision: str | None = "db3c91261770"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

pattern = r"wf-(?P<hex>[0-9a-f]{32})"


def wf_id_to_uuid(wf_id: str) -> uuid.UUID:
    """Convert a hex string to a UUID string format.

    Args:
        hex_str: The hex string to convert (e.g., '7b8ea32815e544b6af9a38f79fa03622')

    Returns:
        str: UUID formatted string (e.g., '7b8ea328-15e5-44b6-af9a-38f79fa03622')
    """
    match = re.match(pattern, wf_id)
    if not match:
        raise ValueError(f"Invalid workflow ID: {wf_id}")

    hex_str = match.group("hex")
    return uuid.UUID(hex=hex_str)


HAS_ONDELETE_CONSTRAINT = {
    "action": True,
    "schedule": True,
    "webhook": True,
    "workflowdefinition": True,
    "workflowtag": False,
}


def upgrade() -> None:
    connection = op.get_bind()

    logger.info("Dropping all foreign key constraints")
    # First, drop all foreign key constraints
    tables = ["action", "schedule", "webhook", "workflowdefinition", "workflowtag"]
    for table in tables:
        fk_name = f"{table}_workflow_id_fkey"
        op.drop_constraint(fk_name, table, type_="foreignkey")

    logger.info("Converting workflow IDs from hex to UUID format")
    # Create a custom cast function for the conversion
    connection.execute(
        sa.text("""
        CREATE OR REPLACE FUNCTION hex_to_uuid(hex_id TEXT) RETURNS UUID AS $$
        DECLARE
            hex_part TEXT;
        BEGIN
            hex_part = substring(hex_id from 4); -- Remove 'wf-' prefix
            RETURN hex_part::uuid;
        END;
        $$ LANGUAGE plpgsql;
    """)
    )

    # Alter the column type directly with the conversion
    op.execute("ALTER TABLE workflow ALTER COLUMN id TYPE UUID USING hex_to_uuid(id)")

    # Convert related tables
    for table in tables:
        logger.info(f"Converting {table} workflow_id to UUID")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN workflow_id TYPE UUID USING hex_to_uuid(workflow_id)"
        )

        # Recreate foreign key constraint
        op.create_foreign_key(
            f"{table}_workflow_id_fkey",
            table,
            "workflow",
            ["workflow_id"],
            ["id"],
            ondelete="CASCADE" if HAS_ONDELETE_CONSTRAINT[table] else None,
        )

    # Clean up the conversion function
    connection.execute(sa.text("DROP FUNCTION hex_to_uuid(TEXT)"))


def downgrade() -> None:
    connection = op.get_bind()

    logger.info("Dropping all foreign key constraints")
    tables = ["action", "schedule", "webhook", "workflowdefinition", "workflowtag"]
    for table in tables:
        fk_name = f"{table}_workflow_id_fkey"
        op.drop_constraint(fk_name, table, type_="foreignkey")

    logger.info("Converting workflow IDs from UUID back to hex format")
    # Create a custom cast function for the conversion
    connection.execute(
        sa.text("""
        CREATE OR REPLACE FUNCTION uuid_to_hex(id UUID) RETURNS TEXT AS $$
        BEGIN
            RETURN 'wf-' || replace(id::text, '-', '');
        END;
        $$ LANGUAGE plpgsql;
    """)
    )

    # Alter column types directly with the conversion
    op.execute(
        "ALTER TABLE workflow ALTER COLUMN id TYPE VARCHAR USING uuid_to_hex(id)"
    )

    # Convert related tables
    for table in tables:
        logger.info(f"Converting {table} workflow_id back to hex format")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN workflow_id TYPE VARCHAR USING uuid_to_hex(workflow_id)"
        )

        # Recreate foreign key constraint
        op.create_foreign_key(
            f"{table}_workflow_id_fkey",
            table,
            "workflow",
            ["workflow_id"],
            ["id"],
        )

    # Clean up the conversion function
    connection.execute(sa.text("DROP FUNCTION uuid_to_hex(UUID)"))
