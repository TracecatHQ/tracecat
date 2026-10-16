"""Add action.locked_execution_id.

Revision ID: 3b8f2c1d9e7a
Revises: 7e666328c9a6
Create Date: 2026-10-15 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3b8f2c1d9e7a"
down_revision: str | None = "7e666328c9a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "action", sa.Column("locked_execution_id", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("action", "locked_execution_id")
