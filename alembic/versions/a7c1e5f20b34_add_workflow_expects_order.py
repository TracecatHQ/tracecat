"""Add workflow.expects_order.

Revision ID: a7c1e5f20b34
Revises: d9107f689ccf
Create Date: 2026-10-12 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7c1e5f20b34"
down_revision: str | None = "d9107f689ccf"
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
