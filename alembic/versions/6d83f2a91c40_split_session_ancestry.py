"""Separate spawning ancestry from captured history forks.

Revision ID: 6d83f2a91c40
Revises: acbacbf8ef53
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6d83f2a91c40"
down_revision: str | None = "acbacbf8ef53"
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
        ),
        parent_session_id = NULL
    FROM agent_session AS source
    WHERE source.id = child.parent_session_id
      AND source.workspace_id = child.workspace_id
"""


def upgrade() -> None:
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
    # Before this revision parent_session_id exclusively denoted a fork. Use
    # creation time to recover the closest available historical boundary.
    op.execute(sa.text(BACKFILL_SQL))
    op.execute(
        sa.text(
            "UPDATE agent_session SET parent_session_id = NULL "
            "WHERE parent_session_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Session ancestry cannot be safely merged back into one column; "
        "restore a database snapshot before rolling back the application."
    )
