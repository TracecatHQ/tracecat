"""migrate_table_columns_integer_to_bigint

Revision ID: c23dbe59fec6
Revises: 70144f614d3d
Create Date: 2025-11-15 09:16:38.093900

"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa

from alembic import op
from tracecat.identifiers.workflow import WorkspaceUUID

# revision identifiers, used by Alembic.
revision: str = "c23dbe59fec6"
down_revision: str | None = "70144f614d3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema_for_owner(owner_id: UUID | str | None) -> str | None:
    """Derive the physical schema name for a workspace-owned table."""
    if owner_id is None:
        return None
    workspace_id = WorkspaceUUID.new(owner_id)
    return f"tables_{workspace_id.short()}"


def upgrade() -> None:
    """Migrate existing INTEGER columns in user-defined tables to BIGINT."""
    connection = op.get_bind()
    result = connection.execute(
        sa.text(
            """
            SELECT
                tc.id as column_id,
                tc.name as column_name,
                t.name as table_name,
                t.owner_id
            FROM table_columns tc
            JOIN tables t ON tc.table_id = t.id
            WHERE tc.type = 'INTEGER'
        """
        )
    )

    columns = result.fetchall()

    for column in columns:
        schema_name = _schema_for_owner(column.owner_id)
        if schema_name is None:
            continue

        op.alter_column(
            column.table_name,
            column.column_name,
            schema=schema_name,
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
        )
        op.execute(
            f"UPDATE table_columns SET type = 'BIGINT' WHERE id = '{column.column_id}'"
        )


def downgrade() -> None:
    """Revert BIGINT columns back to INTEGER."""
    connection = op.get_bind()
    result = connection.execute(
        sa.text(
            """
            SELECT
                tc.id as column_id,
                tc.name as column_name,
                t.name as table_name,
                t.owner_id
            FROM table_columns tc
            JOIN tables t ON tc.table_id = t.id
            WHERE tc.type = 'BIGINT'
        """
        )
    )

    columns = result.fetchall()

    for column in columns:
        schema_name = _schema_for_owner(column.owner_id)
        if schema_name is None:
            continue
        op.alter_column(
            column.table_name,
            column.column_name,
            schema=schema_name,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
        )
        connection.execute(
            f"UPDATE table_columns SET type = 'INTEGER' WHERE id = '{column.column_id}'"
        )
