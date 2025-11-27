"""fix_bigint_metadata_to_integer

Fix table_columns metadata that was incorrectly set to 'BIGINT' by migration c23dbe59fec6.
The physical PostgreSQL columns should remain BIGINT, but the metadata should use 'INTEGER'
(the Tracecat SqlType enum value).

Revision ID: a81bc08c39a1
Revises: 3d3c6f1f8c0a
Create Date: 2025-11-27 10:14:17.606168

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a81bc08c39a1"
down_revision: str | None = "3d3c6f1f8c0a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Fix table_columns metadata: BIGINT -> INTEGER."""
    # Update any columns that have 'BIGINT' in metadata back to 'INTEGER'
    # This fixes columns affected by the buggy version of migration c23dbe59fec6
    op.execute(
        sa.text(
            """
            UPDATE table_columns
            SET type = 'INTEGER'
            WHERE type = 'BIGINT'
            """
        )
    )


def downgrade() -> None:
    """Revert INTEGER back to BIGINT in metadata."""
    # This would only affect columns that were fixed by this migration
    # Note: We can't distinguish which columns were affected, so we don't
    # revert anything. This is safe because having 'INTEGER' in metadata
    # while physical column is BIGINT is the correct state.
    pass
