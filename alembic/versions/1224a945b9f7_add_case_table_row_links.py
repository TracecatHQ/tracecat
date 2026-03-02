"""add case table row links

Revision ID: 1224a945b9f7
Revises: c9e4f54f0a2b
Create Date: 2026-02-27 23:08:20.288499

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1224a945b9f7"
down_revision: str | None = "c9e4f54f0a2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_table_row",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["case.id"],
            name=op.f("fk_case_table_row_case_id_case"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["tables.id"],
            name=op.f("fk_case_table_row_table_id_tables"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_case_table_row_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_case_table_row")),
        sa.UniqueConstraint(
            "case_id", "table_id", "row_id", name="uq_case_table_row_link"
        ),
    )
    op.create_index(
        "ix_case_table_row_case_id", "case_table_row", ["case_id"], unique=False
    )
    op.create_index(op.f("ix_case_table_row_id"), "case_table_row", ["id"], unique=True)
    op.create_index(
        "ix_case_table_row_row_id", "case_table_row", ["row_id"], unique=False
    )
    op.create_index(
        "ix_case_table_row_table_id", "case_table_row", ["table_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_case_table_row_table_id", table_name="case_table_row")
    op.drop_index("ix_case_table_row_row_id", table_name="case_table_row")
    op.drop_index(op.f("ix_case_table_row_id"), table_name="case_table_row")
    op.drop_index("ix_case_table_row_case_id", table_name="case_table_row")
    op.drop_table("case_table_row")
