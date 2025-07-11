"""rename_decimal_to_numeric_in_table_columns

Revision ID: 419454d1c5c5
Revises: 9a001807d27b
Create Date: 2025-07-05 21:25:41.827778

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "419454d1c5c5"
down_revision: str | None = "9a001807d27b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Update all DECIMAL type values to NUMERIC in table_columns
    op.execute(
        """
        UPDATE table_columns
        SET type = 'NUMERIC'
        WHERE type = 'DECIMAL'
        """
    )


def downgrade() -> None:
    # Revert NUMERIC back to DECIMAL
    op.execute(
        """
        UPDATE table_columns
        SET type = 'DECIMAL'
        WHERE type = 'NUMERIC'
        """
    )
