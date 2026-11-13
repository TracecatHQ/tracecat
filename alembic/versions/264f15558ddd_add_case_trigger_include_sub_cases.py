"""Add case_trigger.include_sub_cases.

Revision ID: 264f15558ddd
Revises: ec392d96f2b0
Create Date: 2026-11-12 18:45:00.000000

Case triggers fire only on top-level cases unless this is set, so existing
triggers stop firing on sub-case events.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "264f15558ddd"
down_revision: str | None = "ec392d96f2b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "case_trigger",
        sa.Column(
            "include_sub_cases",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("case_trigger", "include_sub_cases")
