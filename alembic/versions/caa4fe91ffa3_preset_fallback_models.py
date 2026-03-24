"""preset fallback models

Revision ID: caa4fe91ffa3
Revises: 0a1e3100a432
Create Date: 2026-03-24 10:05:32.541220

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "caa4fe91ffa3"
down_revision: str | None = "0a1e3100a432"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_preset",
        sa.Column(
            "fallback_models", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.add_column(
        "agent_preset_version",
        sa.Column(
            "fallback_models", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_preset_version", "fallback_models")
    op.drop_column("agent_preset", "fallback_models")
