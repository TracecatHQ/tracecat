"""add agent subagent config

Revision ID: 9f0b5f6a2d1c
Revises: 548aa7691799
Create Date: 2026-04-23 19:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f0b5f6a2d1c"
down_revision: str | None = "548aa7691799"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DISABLED_AGENTS_SQL = sa.text("'{\"enabled\": false}'::jsonb")


def upgrade() -> None:
    op.add_column(
        "agent_preset",
        sa.Column(
            "agents",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_DISABLED_AGENTS_SQL,
            nullable=False,
        ),
    )
    op.add_column(
        "agent_preset_version",
        sa.Column(
            "agents",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_DISABLED_AGENTS_SQL,
            nullable=False,
        ),
    )
    op.add_column(
        "agent_session",
        sa.Column(
            "agents_binding",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_session", "agents_binding")
    op.drop_column("agent_preset_version", "agents")
    op.drop_column("agent_preset", "agents")
