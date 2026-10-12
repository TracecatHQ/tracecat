"""Add workspace_tunnel.encrypted_gateway_key.

Revision ID: 8b51e0c47d2a
Revises: 3f2a9c1d7b4e
Create Date: 2026-10-12 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

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


def downgrade() -> None:
    op.drop_column("workspace_tunnel", "encrypted_gateway_key")
