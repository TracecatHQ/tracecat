"""add_agent_session_work_dir_snapshot

Revision ID: b7f0d1e2c3a4
Revises: 03dbf6e4b31f
Create Date: 2026-05-28 03:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7f0d1e2c3a4"
down_revision: str | None = "03dbf6e4b31f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_session",
        sa.Column(
            "work_dir_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_session", "work_dir_snapshot")
