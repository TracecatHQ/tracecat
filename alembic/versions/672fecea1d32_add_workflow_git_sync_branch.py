"""add workflow git sync branch

Revision ID: 672fecea1d32
Revises: 8bec3e244487
Create Date: 2026-02-20 22:39:21.867394

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "672fecea1d32"
down_revision: str | None = "8bec3e244487"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workflow", sa.Column("git_sync_branch", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("workflow", "git_sync_branch")
