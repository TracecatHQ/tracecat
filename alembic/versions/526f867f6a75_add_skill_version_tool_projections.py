"""Add skill version tool projections.

Revision ID: 526f867f6a75
Revises: 44d7e75b6f4c
Create Date: 2026-08-26 21:54:17.760352

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "526f867f6a75"
down_revision: str | None = "44d7e75b6f4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_version_tool",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("skill_version_id", sa.UUID(), nullable=False),
        sa.Column("tool_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), nullable=False),
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
            ["skill_version_id"],
            ["skill_version.id"],
            name=op.f("fk_skill_version_tool_skill_version_id_skill_version"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_skill_version_tool_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_skill_version_tool")),
        sa.UniqueConstraint(
            "workspace_id",
            "skill_version_id",
            "tool_id",
            name="uq_skill_version_tool_workspace_version_tool",
        ),
    )
    op.create_index(
        op.f("ix_skill_version_tool_id"),
        "skill_version_tool",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_skill_version_tool_skill_version_id"),
        "skill_version_tool",
        ["skill_version_id"],
        unique=False,
    )

    op.create_table(
        "skill_version_mcp_tool",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("skill_version_id", sa.UUID(), nullable=False),
        sa.Column("tool_id", sa.String(length=255), nullable=False),
        sa.Column("mcp_integration_id", sa.UUID(), nullable=True),
        sa.Column("tool_name", sa.String(length=255), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), nullable=False),
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
            ["mcp_integration_id"],
            ["mcp_integration.id"],
            name=op.f("fk_skill_version_mcp_tool_mcp_integration_id_mcp_integration"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_version.id"],
            name=op.f("fk_skill_version_mcp_tool_skill_version_id_skill_version"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_skill_version_mcp_tool_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_skill_version_mcp_tool")),
        sa.UniqueConstraint(
            "workspace_id",
            "skill_version_id",
            "tool_id",
            name="uq_skill_version_mcp_tool_workspace_version_tool",
        ),
    )
    op.create_index(
        op.f("ix_skill_version_mcp_tool_id"),
        "skill_version_mcp_tool",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_skill_version_mcp_tool_skill_version_id"),
        "skill_version_mcp_tool",
        ["skill_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_skill_version_mcp_tool_mcp_integration_id"),
        "skill_version_mcp_tool",
        ["mcp_integration_id"],
        unique=False,
    )

    op.execute(enable_workspace_table_rls("skill_version_tool"))
    op.execute(enable_workspace_table_rls("skill_version_mcp_tool"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("skill_version_mcp_tool"))
    op.execute(disable_workspace_table_rls("skill_version_tool"))
    op.drop_table("skill_version_mcp_tool")
    op.drop_table("skill_version_tool")
