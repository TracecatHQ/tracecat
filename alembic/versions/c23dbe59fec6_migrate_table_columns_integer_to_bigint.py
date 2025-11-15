"""migrate_table_columns_integer_to_bigint

Revision ID: c23dbe59fec6
Revises: 70144f614d3d
Create Date: 2025-11-15 09:16:38.093900

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c23dbe59fec6"
down_revision: str | None = "70144f614d3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Migrate existing INTEGER columns in user-defined tables to BIGINT."""
    connection = op.get_bind()

    # Get all INTEGER columns from metadata
    result = connection.execute(
        sa.text("""
            SELECT
                tc.name as column_name,
                t.name as table_name,
                t.owner_id
            FROM table_columns tc
            JOIN tables t ON tc.table_id = t.id
            WHERE tc.type = 'INTEGER'
        """)
    )

    columns = result.fetchall()

    # Alter each column type to BIGINT
    for column in columns:
        # Find the schema for this table
        schema_result = connection.execute(
            sa.text("""
                SELECT table_schema
                FROM information_schema.tables
                WHERE table_schema LIKE 'tables_%'
                  AND table_name = :table_name
                LIMIT 1
            """),
            {"table_name": column.table_name},
        )
        schema_row = schema_result.fetchone()

        if schema_row:
            # Use Alembic helper to emit a properly quoted DDL statement
            op.alter_column(
                column.table_name,
                column.column_name,
                schema=schema_row.table_schema,
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
            )


def downgrade() -> None:
    """Revert BIGINT columns back to INTEGER."""
    connection = op.get_bind()

    # Get all INTEGER columns from metadata
    result = connection.execute(
        sa.text("""
            SELECT
                tc.name as column_name,
                t.name as table_name,
                t.owner_id
            FROM table_columns tc
            JOIN tables t ON tc.table_id = t.id
            WHERE tc.type = 'INTEGER'
        """)
    )

    columns = result.fetchall()

    # Alter each column type back to INTEGER
    for column in columns:
        # Find the schema for this table
        schema_result = connection.execute(
            sa.text("""
                SELECT table_schema
                FROM information_schema.tables
                WHERE table_schema LIKE 'tables_%'
                  AND table_name = :table_name
                LIMIT 1
            """),
            {"table_name": column.table_name},
        )
        schema_row = schema_result.fetchone()

        if schema_row:
            op.alter_column(
                column.table_name,
                column.column_name,
                schema=schema_row.table_schema,
                existing_type=sa.BigInteger(),
                type_=sa.Integer(),
            )
