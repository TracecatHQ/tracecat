"""Add client metadata columns to access_token

Revision ID: a7c3e9f1b2d4
Revises: 31ee4b7f175a
Create Date: 2026-09-14 20:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3e9f1b2d4"
down_revision: str | None = "31ee4b7f175a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "access_token",
        sa.Column("ip_address", sa.String(length=45), nullable=True),
    )
    op.add_column(
        "access_token",
        sa.Column("user_agent", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "access_token",
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("access_token", "last_seen_at")
    op.drop_column("access_token", "user_agent")
    op.drop_column("access_token", "ip_address")
