"""Add trusted API base URL to OAuth integrations.

Revision ID: d7e9f1a2b3c4
Revises: c6a8d4f3b2e1
Create Date: 2026-07-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e9f1a2b3c4"
down_revision: str | None = "c6a8d4f3b2e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add an expand-safe nullable API base URL column."""
    op.add_column(
        "oauth_integration",
        sa.Column("api_base_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove the API base URL column."""
    op.drop_column("oauth_integration", "api_base_url")
