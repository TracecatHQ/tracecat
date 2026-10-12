"""Add workspace_tunnel gateway key and managed Pangolin columns.

Revision ID: 8b51e0c47d2a
Revises: 3f2a9c1d7b4e
Create Date: 2026-10-12 19:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "8b51e0c47d2a"
down_revision: str | None = "3f2a9c1d7b4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_tunnel",
        sa.Column("encrypted_gateway_key", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "workspace_tunnel",
        sa.Column(
            "managed", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column(
        "workspace_tunnel",
        sa.Column("pangolin_site_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "workspace_tunnel",
        sa.Column("pangolin_client_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "workspace_tunnel",
        sa.Column(
            "pangolin_resource_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    for column in (
        "pangolin_resource_ids",
        "pangolin_client_id",
        "pangolin_site_id",
        "managed",
        "encrypted_gateway_key",
    ):
        op.drop_column("workspace_tunnel", column)
