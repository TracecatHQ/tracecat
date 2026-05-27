"""add curr_run_id to agent_session_history

Revision ID: 243a597b6a3a
Revises: e1a2b3c4d5f6
Create Date: 2026-05-27 10:43:33.204849

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "243a597b6a3a"
down_revision: str | None = "e1a2b3c4d5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
