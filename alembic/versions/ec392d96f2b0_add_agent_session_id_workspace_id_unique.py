"""Add a unique key on agent_session (id, workspace_id).

Revision ID: ec392d96f2b0
Revises: 7c2d9e4f1a3b
Create Date: 2026-11-05 10:00:00.000000

The target of composite foreign keys from enterprise channel tables, so a row
naming a session can never name another workspace. ``id`` is already unique, so
the new key cannot reject any existing or future row.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ec392d96f2b0"
down_revision: str | None = "7c2d9e4f1a3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "uq_agent_session_id_workspace_id"


_INDEX_DEF = (
    f"CREATE UNIQUE INDEX {_CONSTRAINT} ON public.agent_session"
    " USING btree (id, workspace_id)"
)


def _index_ready() -> bool | None:
    """None when absent; False when invalid or not this revision's index."""
    return (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT i.indisvalid AND i.indisready AND pg_get_indexdef(i.indexrelid) = :def
        FROM pg_index i
        WHERE i.indexrelid = to_regclass(:name)
    """),
            {"name": f"public.{_CONSTRAINT}", "def": _INDEX_DEF},
        )
        .scalar_one_or_none()
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        ready = _index_ready()
        # A retry keeps a built index; it rebuilds one a failed build left invalid.
        if ready is False:
            op.execute(f'DROP INDEX CONCURRENTLY "{_CONSTRAINT}"')
        if not ready:
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
