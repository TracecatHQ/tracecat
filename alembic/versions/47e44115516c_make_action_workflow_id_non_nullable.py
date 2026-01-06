"""make action workflow_id non-nullable

Revision ID: 47e44115516c
Revises: c4c234b396bd
Create Date: 2026-01-06 16:45:52.365012

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "47e44115516c"
down_revision: str | None = "c4c234b396bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Delete orphan actions (those without a workflow) before adding NOT NULL constraint
    op.execute("DELETE FROM action WHERE workflow_id IS NULL")
    op.alter_column("action", "workflow_id", existing_type=sa.UUID(), nullable=False)


def downgrade() -> None:
    op.alter_column("action", "workflow_id", existing_type=sa.UUID(), nullable=True)
