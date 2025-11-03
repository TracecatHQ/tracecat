"""Add agent profile table

Revision ID: a5b6c7d8e9f0
Revises: b376e6d16619
Create Date: 2025-02-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: str | None = "b376e6d16619"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_profile",
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
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("slug", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("model_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model_provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("base_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column(
            "output_type",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "actions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "namespaces",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "fixed_arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "tool_approvals",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("mcp_server_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column(
            "mcp_server_headers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "model_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "retries",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("surrogate_id"),
        sa.UniqueConstraint("owner_id", "slug", name="uq_agent_profile_owner_slug"),
    )
    op.create_index(op.f("ix_agent_profile_id"), "agent_profile", ["id"], unique=True)
    op.create_index(
        op.f("ix_agent_profile_slug"), "agent_profile", ["slug"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_profile_slug"), table_name="agent_profile")
    op.drop_index(op.f("ix_agent_profile_id"), table_name="agent_profile")
    op.drop_table("agent_profile")
