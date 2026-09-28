"""add agent session last_error

Adds a terminal error summary (``last_error``) to ``agent_session``. This is the
sole persisted run-outcome signal: it is present iff the most recent run ended
in error and is cleared when the next turn starts. The inbox reads it to route a
session into its Error group instead of describing a fail-fast Temporal
execution, which surfaced stale errors on old, healthy sessions.

Errors are run-ending, so the latest error is always the latest outcome; no
separate status enum is needed. A partial index supports the only predicate the
inbox uses (``last_error IS NOT NULL``) without indexing the text payload.

Purely additive: the column is nullable and unbackfilled. A NULL last_error
means "no recorded error" (legacy / never-run / clean), so existing sessions are
not flipped into the Error group. The turn lifecycle populates it going forward.

Revision ID: e32940d12293
Revises: 574d19d8c16c
Create Date: 2026-06-30 14:10:33.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e32940d12293"
down_revision: str | None = "574d19d8c16c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_session",
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    # Partial index: the inbox only ever filters on presence, so index the
    # errored rows (a small minority) rather than the text values.
    op.create_index(
        "ix_agent_session_last_error_present",
        "agent_session",
        ["id"],
        unique=False,
        postgresql_where=sa.text("last_error IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_session_last_error_present", table_name="agent_session")
    op.drop_column("agent_session", "last_error")
