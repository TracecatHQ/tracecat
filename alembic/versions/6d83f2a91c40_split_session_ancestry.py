"""Separate spawning ancestry from captured history forks.

Revision ID: 6d83f2a91c40
Revises: a667d946cca1
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6d83f2a91c40"
down_revision: str | None = "a667d946cca1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL_SQL = """
    UPDATE agent_session AS child
    SET forked_from_session_id = child.parent_session_id,
        forked_from_sdk_session_id = source.sdk_session_id,
        forked_from_history_id = (
            SELECT max(history.surrogate_id)
            FROM agent_session_history AS history
            WHERE history.session_id = child.parent_session_id
              AND history.created_at <= child.created_at
        )
    FROM agent_session AS source
    WHERE source.id = child.parent_session_id
      AND source.workspace_id = child.workspace_id
"""

# Older application instances still insert forks using only parent_session_id.
# Capture their fork state at insertion, including an empty history boundary,
# rather than reconstructing it from a source that may have advanced later.
CAPTURE_LEGACY_FORK_SQL = """
    CREATE FUNCTION capture_legacy_agent_session_fork() RETURNS trigger AS $$
    BEGIN
        IF NEW.forked_from_session_id IS NOT NULL THEN
            NEW.parent_session_id := NEW.forked_from_session_id;
        ELSIF NEW.parent_session_id IS NOT NULL THEN
            SELECT source.id, source.sdk_session_id, (
                SELECT max(history.surrogate_id)
                FROM agent_session_history AS history
                WHERE history.session_id = source.id
            )
            INTO NEW.forked_from_session_id,
                 NEW.forked_from_sdk_session_id,
                 NEW.forked_from_history_id
            FROM agent_session AS source
            WHERE source.id = NEW.parent_session_id
              AND source.workspace_id = NEW.workspace_id;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.add_column(
        "agent_session", sa.Column("spawned_by_session_id", sa.UUID(), nullable=True)
    )
    op.create_index(
        op.f("ix_agent_session_spawned_by_session_id"),
        "agent_session",
        ["spawned_by_session_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_agent_session_spawned_by_session_id_agent_session"),
        "agent_session",
        "agent_session",
        ["spawned_by_session_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )
    op.add_column(
        "agent_session", sa.Column("forked_from_session_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "agent_session",
        sa.Column("forked_from_history_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_session",
        sa.Column("forked_from_sdk_session_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        op.f("ix_agent_session_forked_from_session_id"),
        "agent_session",
        ["forked_from_session_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_agent_session_forked_from_session_id_agent_session"),
        "agent_session",
        "agent_session",
        ["forked_from_session_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )
    # Keep parent_session_id as a fork source throughout the compatibility
    # window. Use creation time to recover the closest historical boundary.
    op.execute(sa.text(BACKFILL_SQL))
    op.execute(sa.text(CAPTURE_LEGACY_FORK_SQL))
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_capture_legacy_agent_session_fork "
            "BEFORE INSERT ON agent_session FOR EACH ROW "
            "EXECUTE FUNCTION capture_legacy_agent_session_fork()"
        )
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Session ancestry migrations are roll-forward-only. "
        "Apply a forward migration to repair the schema."
    )
