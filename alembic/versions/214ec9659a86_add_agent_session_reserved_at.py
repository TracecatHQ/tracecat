"""Add agent_session.reserved_run_id and reserved_at.

Revision ID: 214ec9659a86
Revises: 8b51e0c47d2a
Create Date: 2026-10-13 21:16:37.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "214ec9659a86"
down_revision: str | None = "8b51e0c47d2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive and nullable: the previous app version ignores them, and existing
    # reservations stay NULL, which turn recovery never tombstones.
    op.add_column(
        "agent_session",
        sa.Column("reserved_run_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "agent_session",
        sa.Column("reserved_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_session", "reserved_at")
    op.drop_column("agent_session", "reserved_run_id")
