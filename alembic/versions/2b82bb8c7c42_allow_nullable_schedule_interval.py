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
    # Check if there are any schedules with NULL 'every' values
    # that also don't have a 'cron' expression (which would be invalid)
    result = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM schedule WHERE every IS NULL AND cron IS NULL")
    )
    invalid_count = result.scalar()

    if invalid_count and invalid_count > 0:
        raise ValueError(
            f"Cannot downgrade: {invalid_count} schedule(s) have both 'every' and 'cron' as NULL. "
            "Please manually handle these records before downgrading:\n"
            "  - Option 1: DELETE FROM schedule WHERE every IS NULL AND cron IS NULL;\n"
            "  - Option 2: Set a default interval: UPDATE schedule SET every = INTERVAL '1 day' WHERE every IS NULL AND cron IS NULL;\n"
            "  - Option 3: Set a cron expression for these schedules"
        )

    # For schedules that use cron instead of every, set a default interval
    # This ensures we can make the column NOT NULL
    op.execute(
        "UPDATE schedule SET every = INTERVAL '1 day' WHERE every IS NULL AND cron IS NOT NULL"
    )

    # Now safely alter the column to NOT NULL
    op.alter_column(
        "schedule",
        "every",
        existing_type=sa.Interval(),
        nullable=False,
    )
