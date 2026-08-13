"""Preserve staged skill metadata.

Revision ID: ff35596086bd
Revises: c20c04c7d2a9
Create Date: 2026-08-12 22:23:53.142316

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff35596086bd"
down_revision: str | None = "c20c04c7d2a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skill_upload",
        sa.Column("expected_skill_name", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "skill_upload",
        sa.Column("expected_skill_description", sa.String(length=4000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("skill_upload", "expected_skill_description")
    op.drop_column("skill_upload", "expected_skill_name")
