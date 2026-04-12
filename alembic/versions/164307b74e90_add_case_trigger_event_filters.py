"""add case trigger event filters

Revision ID: 164307b74e90
Revises: 0a1e3100a432
Create Date: 2026-03-18 18:04:10.746919

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "164307b74e90"
down_revision: str | None = "0a1e3100a432"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "case_trigger",
        sa.Column(
            "event_filters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("case_trigger", "event_filters")
