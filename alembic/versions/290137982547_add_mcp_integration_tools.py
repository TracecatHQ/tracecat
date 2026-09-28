"""add mcp integration tools

Revision ID: 290137982547
Revises: 9b52f7f18a31
Create Date: 2026-06-10 15:03:58.463176

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "290137982547"
down_revision: str | None = "9b52f7f18a31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_integration",
        sa.Column("tools", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mcp_integration", "tools")
