"""add skill deleted_at

Revision ID: 8b4f6c2d1a9e
Revises: 32b7a1f4d9c2
Create Date: 2026-07-08 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b4f6c2d1a9e"
down_revision: str | None = "32b7a1f4d9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skill",
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE skill
        SET deleted_at = archived_at
        WHERE archived_at IS NOT NULL
        """
    )


def downgrade() -> None:
    """Drop deleted_at; archived_at remains the expand-release source of truth."""
    op.drop_column("skill", "deleted_at")
