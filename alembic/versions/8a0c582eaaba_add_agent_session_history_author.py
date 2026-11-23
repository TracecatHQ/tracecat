"""Add the author and origin of a user prompt to agent session history

Revision ID: 8a0c582eaaba
Revises: 997fd6b8f9f4
Create Date: 2026-11-22 12:00:00.000000

Adds nullable ``agent_session_history.author_id``, the Tracecat user who sent a
user prompt, and nullable ``origin``, where it entered the session (Tracecat
or channel ingress). Additive: the previous app ignores the columns and existing
rows stay null. The partial index for the ON DELETE SET NULL lookup is built
concurrently in the next revision.

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8a0c582eaaba"
down_revision: str | None = "997fd6b8f9f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Fail fast instead of queueing history writes behind a long transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "agent_session_history",
        sa.Column("author_id", sa.UUID(), nullable=True),
    )
    # NOT VALID skips the table scan under ACCESS EXCLUSIVE; existing rows are NULL.
    op.execute(
        "ALTER TABLE agent_session_history "
        "ADD CONSTRAINT fk_agent_session_history_author_id_user "
        'FOREIGN KEY (author_id) REFERENCES "user" (id) '
        "ON DELETE SET NULL NOT VALID"
    )
    op.add_column(
        "agent_session_history",
        sa.Column("origin", sa.String(length=32), nullable=True),
    )
    # NOT VALID for the same reason; the new column is NULL on every row.
    op.execute(
        "ALTER TABLE agent_session_history "
        "ADD CONSTRAINT ck_agent_session_history_origin "
        "CHECK (origin IN ('tracecat', 'channel')) NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_session_history_origin"),
        "agent_session_history",
        type_="check",
    )
    op.drop_column("agent_session_history", "origin")
    op.drop_constraint(
        op.f("fk_agent_session_history_author_id_user"),
        "agent_session_history",
        type_="foreignkey",
    )
    op.drop_column("agent_session_history", "author_id")
