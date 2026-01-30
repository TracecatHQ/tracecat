"""add_dropdown_value_changed_event_type

Revision ID: b4de178d5817
Revises: 5f94298e9430
Create Date: 2026-01-30 12:05:11.730147

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4de178d5817"
down_revision: str | None = "5f94298e9430"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE caseeventtype ADD VALUE IF NOT EXISTS 'DROPDOWN_VALUE_CHANGED'"
    )


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values
    pass
