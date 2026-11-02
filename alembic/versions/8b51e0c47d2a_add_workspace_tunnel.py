"""Add workspace_tunnel table.

Revision ID: 8b51e0c47d2a
Revises: d9107f689ccf
Create Date: 2026-10-12 19:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

revision: str = "8b51e0c47d2a"
down_revision: str | None = "d9107f689ccf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_tunnel",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column(
            "provider",
            sa.String(length=64),
            server_default="pangolin",
            nullable=False,
        ),
        sa.Column("endpoint", sa.String(length=2048), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("encrypted_client_secret", sa.LargeBinary(), nullable=False),
        sa.Column(
            "allowed_targets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("encrypted_gateway_key", sa.LargeBinary(), nullable=True),
        sa.Column(
            "managed", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("pangolin_site_id", sa.Integer(), nullable=True),
        sa.Column("pangolin_client_id", sa.Integer(), nullable=True),
        sa.Column(
            "pangolin_resource_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
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
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_workspace_tunnel_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_workspace_tunnel")),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name=op.f("uq_workspace_tunnel_workspace_id_name"),
        ),
    )
    op.create_index(
        op.f("ix_workspace_tunnel_id"), "workspace_tunnel", ["id"], unique=True
    )
    op.execute(enable_workspace_table_rls("workspace_tunnel"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("workspace_tunnel"))
    op.drop_index(op.f("ix_workspace_tunnel_id"), table_name="workspace_tunnel")
    op.drop_table("workspace_tunnel")
