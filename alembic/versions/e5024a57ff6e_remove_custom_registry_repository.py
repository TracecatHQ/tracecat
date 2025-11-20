"""Remove legacy custom registry repository

Revision ID: e5024a57ff6e
Revises: fe20e84914ad
Create Date: 2026-01-14 00:00:00.000000

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5024a57ff6e"
down_revision: str | None = "fe20e84914ad"
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
    bind: Connection = (
        op.get_bind()
    )  # Acquire a connection that supports parameter binding.
    insert_stmt = sa.text(
        "INSERT INTO registryrepository (id, origin, last_synced_at, commit_sha) "
        "VALUES (:id, 'custom', NULL, NULL) ON CONFLICT (origin) DO NOTHING"
    )
    bind.execute(
        insert_stmt, {"id": str(uuid.uuid4())}
    )  # Use parameters to avoid string interpolation.
