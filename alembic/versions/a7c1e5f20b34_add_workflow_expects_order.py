"""Add workflow.expects_order.

Revision ID: a7c1e5f20b34
Revises: 8b51e0c47d2a
Create Date: 2026-10-12 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7c1e5f20b34"
down_revision: str | None = "8b51e0c47d2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow",
        sa.Column(
            "expects_order",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow", "expects_order")
