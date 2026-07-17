"""add oauth_state encrypted_pending_config

Revision ID: c4e1a9f7b28d
Revises: c6a8d4f3b2e1
Create Date: 2026-07-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e1a9f7b28d"
down_revision: str | None = "c6a8d4f3b2e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "oauth_state",
        sa.Column("encrypted_pending_config", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("oauth_state", "encrypted_pending_config")
