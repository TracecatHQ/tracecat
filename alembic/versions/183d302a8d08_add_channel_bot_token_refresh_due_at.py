"""Add a plaintext token refresh due time to channel bots

Revision ID: 183d302a8d08
Revises: 43d161879873
Create Date: 2026-11-15 12:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "183d302a8d08"
down_revision: str | None = "43d161879873"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One row per installed bot, so a plain index build is brief; fail fast rather
    # than queue behind a long transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "agent_channel_bot",
        sa.Column("token_refresh_due_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_channel_bot_token_refresh_due_at",
        "agent_channel_bot",
        ["token_refresh_due_at"],
        unique=False,
        postgresql_where=sa.text("token_refresh_due_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_index(
        "ix_agent_channel_bot_token_refresh_due_at",
        table_name="agent_channel_bot",
        postgresql_where=sa.text("token_refresh_due_at IS NOT NULL"),
    )
    op.drop_column("agent_channel_bot", "token_refresh_due_at")
