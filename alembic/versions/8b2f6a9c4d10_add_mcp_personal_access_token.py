"""add mcp personal access token table

Revision ID: 8b2f6a9c4d10
Revises: 96470fdcc686
Create Date: 2026-05-04 16:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_org_optional_workspace_table_rls,
    enable_org_optional_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "8b2f6a9c4d10"
down_revision: str | None = "96470fdcc686"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_personal_access_token",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("hashed", sa.String(length=128), nullable=False),
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("preview", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("revoked_by", sa.UUID(), nullable=True),
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
            ["created_by"],
            ["user.id"],
            name=op.f("fk_mcp_personal_access_token_created_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_mcp_personal_access_token_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["user.id"],
            name=op.f("fk_mcp_personal_access_token_revoked_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_mcp_personal_access_token_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_mcp_personal_access_token_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_mcp_personal_access_token")
        ),
    )
    op.create_index(
        op.f("ix_mcp_personal_access_token_id"),
        "mcp_personal_access_token",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_mcp_personal_access_token_key_id"),
        "mcp_personal_access_token",
        ["key_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_mcp_personal_access_token_organization_id"),
        "mcp_personal_access_token",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mcp_personal_access_token_user_id"),
        "mcp_personal_access_token",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mcp_personal_access_token_workspace_id"),
        "mcp_personal_access_token",
        ["workspace_id"],
        unique=False,
    )

    op.execute(enable_org_optional_workspace_table_rls("mcp_personal_access_token"))


def downgrade() -> None:
    op.execute(disable_org_optional_workspace_table_rls("mcp_personal_access_token"))
    op.drop_index(
        op.f("ix_mcp_personal_access_token_workspace_id"),
        table_name="mcp_personal_access_token",
    )
    op.drop_index(
        op.f("ix_mcp_personal_access_token_user_id"),
        table_name="mcp_personal_access_token",
    )
    op.drop_index(
        op.f("ix_mcp_personal_access_token_organization_id"),
        table_name="mcp_personal_access_token",
    )
    op.drop_index(
        op.f("ix_mcp_personal_access_token_key_id"),
        table_name="mcp_personal_access_token",
    )
    op.drop_index(
        op.f("ix_mcp_personal_access_token_id"),
        table_name="mcp_personal_access_token",
    )
    op.drop_table("mcp_personal_access_token")
