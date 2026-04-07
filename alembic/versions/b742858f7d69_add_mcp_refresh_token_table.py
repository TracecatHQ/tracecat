"""add mcp_refresh_token table

Revision ID: b742858f7d69
Revises: 7e1a4d9c2b6f
Create Date: 2026-04-06 18:22:03.384450

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import disable_org_table_rls, enable_org_table_rls

# revision identifiers, used by Alembic.
revision: str = "b742858f7d69"
down_revision: str | None = "7e1a4d9c2b6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_refresh_token",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("encrypted_metadata", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
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
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_mcp_refresh_token_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_mcp_refresh_token_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_mcp_refresh_token")),
    )
    op.create_index(
        "ix_mcp_refresh_token_expires_at",
        "mcp_refresh_token",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_mcp_refresh_token_family_id",
        "mcp_refresh_token",
        ["family_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mcp_refresh_token_id"), "mcp_refresh_token", ["id"], unique=True
    )
    op.create_index(
        op.f("ix_mcp_refresh_token_status"),
        "mcp_refresh_token",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mcp_refresh_token_token_hash"),
        "mcp_refresh_token",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_mcp_refresh_token_user_id"),
        "mcp_refresh_token",
        ["user_id"],
        unique=False,
    )
    op.execute(enable_org_table_rls("mcp_refresh_token"))


def downgrade() -> None:
    op.execute(disable_org_table_rls("mcp_refresh_token"))
    op.drop_index(op.f("ix_mcp_refresh_token_user_id"), table_name="mcp_refresh_token")
    op.drop_index(
        op.f("ix_mcp_refresh_token_token_hash"), table_name="mcp_refresh_token"
    )
    op.drop_index(op.f("ix_mcp_refresh_token_status"), table_name="mcp_refresh_token")
    op.drop_index(op.f("ix_mcp_refresh_token_id"), table_name="mcp_refresh_token")
    op.drop_index("ix_mcp_refresh_token_family_id", table_name="mcp_refresh_token")
    op.drop_index("ix_mcp_refresh_token_expires_at", table_name="mcp_refresh_token")
    op.drop_table("mcp_refresh_token")
