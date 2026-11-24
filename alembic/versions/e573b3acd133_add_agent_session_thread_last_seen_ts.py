"""Add agent_session_thread.last_seen_ts

Revision ID: e573b3acd133
Revises: 443a3e36870f
Create Date: 2026-11-23 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e573b3acd133"
down_revision: str | None = "443a3e36870f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_session_thread",
        sa.Column("last_seen_ts", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_session_thread", "last_seen_ts")
