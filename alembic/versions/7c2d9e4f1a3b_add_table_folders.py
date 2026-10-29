"""add table folders

Revision ID: 7c2d9e4f1a3b
Revises: 21042b4645f1
Create Date: 2026-10-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

revision: str = "7c2d9e4f1a3b"
down_revision: str | None = "21042b4645f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "table_folder",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
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
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_table_folder_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_table_folder")),
        sa.UniqueConstraint(
            "path", "workspace_id", name="uq_table_folder_path_workspace"
        ),
    )
    op.create_index(op.f("ix_table_folder_id"), "table_folder", ["id"], unique=True)
    op.create_index(
        op.f("ix_table_folder_path"), "table_folder", ["path"], unique=False
    )

    op.add_column("tables", sa.Column("folder_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_tables_folder_id_table_folder"),
        "tables",
        "table_folder",
        ["folder_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_tables_workspace_folder",
        "tables",
        ["workspace_id", "folder_id"],
        unique=False,
    )
    op.execute(enable_workspace_table_rls("table_folder"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("table_folder"))
    op.drop_index("ix_tables_workspace_folder", table_name="tables")
    op.drop_constraint(
        op.f("fk_tables_folder_id_table_folder"),
        "tables",
        type_="foreignkey",
    )
    op.drop_column("tables", "folder_id")
    op.drop_index(op.f("ix_table_folder_path"), table_name="table_folder")
    op.drop_index(op.f("ix_table_folder_id"), table_name="table_folder")
    op.drop_table("table_folder")
