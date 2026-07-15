"""add MCP verification result

Revision ID: d7b9e2f4a6c8
Revises: c6a8d4f3b2e1
Create Date: 2026-07-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7b9e2f4a6c8"
down_revision: str | None = "c6a8d4f3b2e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_integration",
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "mcp_integration",
        sa.Column("last_verification_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mcp_integration", "last_verification_error")
    op.drop_column("mcp_integration", "last_verified_at")
