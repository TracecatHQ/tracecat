"""Index agent session history by author

Revision ID: 443a3e36870f
Revises: 8a0c582eaaba
Create Date: 2026-11-23 12:00:00.000000

Serves the ON DELETE SET NULL lookup on user delete. Built concurrently: a
plain build blocks history writes for the whole table scan.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "443a3e36870f"
down_revision: str | None = "8a0c582eaaba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_agent_session_history_author_id"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # A failed concurrent build leaves an invalid index; rebuild it on retry.
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{_INDEX}"')
        op.execute(
            f'CREATE INDEX CONCURRENTLY "{_INDEX}" '
            "ON agent_session_history (author_id) WHERE author_id IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{_INDEX}"')
