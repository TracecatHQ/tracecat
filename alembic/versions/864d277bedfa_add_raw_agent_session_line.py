"""add raw agent session line

Revision ID: 864d277bedfa
Revises: c6a8d4f3b2e1
Create Date: 2026-07-30 11:12:41.817803

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "864d277bedfa"
down_revision: str | None = "c6a8d4f3b2e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_session_history",
        sa.Column("raw_session_line", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_session_history", "raw_session_line")
