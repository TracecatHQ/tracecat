"""add mcp transport

Revision ID: 5cb936f82dd6
Revises: 3758bd2248e6
Create Date: 2026-03-05 18:15:49.872417

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5cb936f82dd6"
down_revision: str | None = "3758bd2248e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_integration",
        sa.Column(
            "transport",
            sa.String(length=16),
            server_default="http",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("mcp_integration", "transport")
