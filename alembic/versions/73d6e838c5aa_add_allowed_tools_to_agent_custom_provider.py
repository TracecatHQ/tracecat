"""add allowed_tools to agent_custom_provider

Revision ID: 73d6e838c5aa
Revises: d0b32dce7f81
Create Date: 2026-05-08 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "73d6e838c5aa"
down_revision: str | None = "d0b32dce7f81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``allowed_tools`` to ``agent_custom_provider``.

    Nullable JSONB list. ``NULL`` means "no override at the source
    level"; the runtime keeps its default (full SDK toolset). An empty
    list ``[]`` is a deliberate "disable all built-in tools" override.
    """
    op.add_column(
        "agent_custom_provider",
        sa.Column(
            "allowed_tools",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_custom_provider", "allowed_tools")
