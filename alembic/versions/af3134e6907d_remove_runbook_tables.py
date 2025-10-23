"""remove runbook tables

Revision ID: af3134e6907d
Revises: d4fd132ccb50
Create Date: 2025-10-22 16:34:59.754600

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "af3134e6907d"
down_revision: str | None = "d4fd132ccb50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop runbook-related tables
    op.drop_table("runbookcaselink")
    op.drop_index("ix_runbook_id", table_name="runbook")
    op.drop_table("runbook")


def downgrade() -> None:
    # Downgrade not supported - runbook feature has been removed
    raise NotImplementedError("Cannot downgrade removal of runbook feature")
