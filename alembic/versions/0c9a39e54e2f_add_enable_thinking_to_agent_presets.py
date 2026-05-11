"""add enable_thinking to agent presets

Revision ID: 0c9a39e54e2f
Revises: b742858f7d69
Create Date: 2026-04-16 19:35:51.588612

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0c9a39e54e2f"
down_revision: str | None = "b742858f7d69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_preset",
        sa.Column(
            "enable_thinking",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "agent_preset_version",
        sa.Column(
            "enable_thinking",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_preset_version", "enable_thinking")
    op.drop_column("agent_preset", "enable_thinking")
