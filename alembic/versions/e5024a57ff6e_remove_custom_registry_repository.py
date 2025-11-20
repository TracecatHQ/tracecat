"""Remove legacy custom registry repository

Revision ID: e5024a57ff6e
Revises: 2ef382e77dea
Create Date: 2026-01-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5024a57ff6e"
down_revision: str | None = "2ef382e77dea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove custom registry repositories and their actions."""
    op.execute(
        sa.text(
            "DELETE FROM registryaction "
            "WHERE repository_id IN (SELECT id FROM registryrepository WHERE origin = 'custom')"
        )
    )  # Clean up any action rows that point at a custom repository.
    op.execute(
        sa.text("DELETE FROM registryrepository WHERE origin = 'custom'")
    )  # Remove the orphaned custom repository itself.


def downgrade() -> None:
    """Recreate the custom registry repository row when rolling back."""
    # No need to recreate the custom registry repository row when rolling back.
    pass
