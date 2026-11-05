"""Add a unique key on agent_session (id, workspace_id).

Revision ID: ec392d96f2b0
Revises: 4ea30f9ea491
Create Date: 2026-11-05 10:00:00.000000

The target of composite foreign keys from enterprise channel tables, so a row
naming a session can never name another workspace. ``id`` is already unique, so
the new key cannot reject any existing or future row.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ec392d96f2b0"
down_revision: str | None = "4ea30f9ea491"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "uq_agent_session_id_workspace_id"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # A failed concurrent build leaves an invalid index behind. Rebuild it
        # so a retry never adopts a partial index.
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{_CONSTRAINT}"')
        op.execute(
            f'CREATE UNIQUE INDEX CONCURRENTLY "{_CONSTRAINT}"'
            " ON agent_session (id, workspace_id)"
        )
    # Attaching the built index is catalog-only but takes ACCESS EXCLUSIVE; fail
    # fast rather than queue session reads behind a long transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(
        f'ALTER TABLE agent_session ADD CONSTRAINT "{_CONSTRAINT}"'
        f' UNIQUE USING INDEX "{_CONSTRAINT}"'
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(f'ALTER TABLE agent_session DROP CONSTRAINT IF EXISTS "{_CONSTRAINT}"')
