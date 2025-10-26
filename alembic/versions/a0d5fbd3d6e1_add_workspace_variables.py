"""Add workspace variables table

Revision ID: a0d5fbd3d6e1
Revises: b6c80a9bb5ce
Create Date: 2024-09-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a0d5fbd3d6e1"
down_revision: str | None = "b6c80a9bb5ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_variable",
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("(now() AT TIME ZONE 'utc'::text)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("(now() AT TIME ZONE 'utc'::text)"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column(
            "values",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "environment",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="default",
        ),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["workspace.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("surrogate_id"),
    )
    op.create_index(
        op.f("ix_workspace_variable_id"),
        "workspace_variable",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_workspace_variable_name"),
        "workspace_variable",
        ["name"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_workspace_variable_name_env_owner",
        "workspace_variable",
        ["name", "environment", "owner_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_workspace_variable_name_env_owner",
        "workspace_variable",
        type_="unique",
    )
    op.drop_index(op.f("ix_workspace_variable_name"), table_name="workspace_variable")
    op.drop_index(op.f("ix_workspace_variable_id"), table_name="workspace_variable")
    op.drop_table("workspace_variable")
