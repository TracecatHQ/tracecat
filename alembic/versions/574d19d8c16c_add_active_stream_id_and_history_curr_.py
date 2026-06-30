"""add active_stream_id and history curr_run_id

Revision ID: 574d19d8c16c
Revises: 31b8cb7b312e
Create Date: 2026-06-22 15:16:14.572223

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "574d19d8c16c"
down_revision: str | None = "31b8cb7b312e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Per-turn stream pivot on the session row.
    op.add_column(
        "agent_session",
        sa.Column("active_stream_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        op.f("ix_agent_session_active_stream_id"),
        "agent_session",
        ["active_stream_id"],
        unique=False,
    )
    # Tag history rows with the producing run so mid-turn loads can hide the
    # active run's partial rows (Redis is the sole live-assistant source).
    op.add_column(
        "agent_session_history",
        sa.Column("curr_run_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        op.f("ix_agent_session_history_curr_run_id"),
        "agent_session_history",
        ["curr_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_agent_session_history_curr_run_id"),
        table_name="agent_session_history",
    )
    op.drop_column("agent_session_history", "curr_run_id")
    op.drop_index(
        op.f("ix_agent_session_active_stream_id"),
        table_name="agent_session",
    )
    op.drop_column("agent_session", "active_stream_id")
