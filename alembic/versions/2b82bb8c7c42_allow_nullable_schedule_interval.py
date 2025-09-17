"""Allow nullable schedule interval

Revision ID: 2b82bb8c7c42
Revises: 0c09410efaac
Create Date: 2025-09-13 15:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2b82bb8c7c42"
down_revision: str | None = "0c09410efaac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "schedule",
        "every",
        existing_type=sa.Interval(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "schedule",
        "every",
        existing_type=sa.Interval(),
        nullable=False,
    )
