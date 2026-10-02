"""add workspace chat overrides

Revision ID: fc7556e84c92
Revises: 6d83f2a91c40
Create Date: 2026-09-22 12:06:21.499043

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fc7556e84c92"
down_revision: str | None = "6d83f2a91c40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_session",
        sa.Column("workspace_chat_overrides", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_session", "workspace_chat_overrides")
