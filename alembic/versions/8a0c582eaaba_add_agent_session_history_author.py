"""Add the author of a user prompt to agent session history

Revision ID: 8a0c582eaaba
Revises: 997fd6b8f9f4
Create Date: 2026-11-22 12:00:00.000000

Adds nullable ``agent_session_history.author_id``, the Tracecat user who sent a
user prompt. Additive: the previous app ignores the column and existing rows
stay null. A partial index serves the ON DELETE SET NULL lookup on user
delete.

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
    op.create_index(
        "ix_agent_session_history_author_id",
        "agent_session_history",
        ["author_id"],
        unique=False,
        postgresql_where=sa.text("author_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_session_history_author_id",
        table_name="agent_session_history",
        postgresql_where=sa.text("author_id IS NOT NULL"),
    )
    op.drop_constraint(
        op.f("fk_agent_session_history_author_id_user"),
        "agent_session_history",
        type_="foreignkey",
    )
    op.drop_column("agent_session_history", "author_id")
