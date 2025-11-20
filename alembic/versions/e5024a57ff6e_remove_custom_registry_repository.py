"""Remove legacy custom registry repository

Revision ID: e5024a57ff6e
Revises: fe20e84914ad
Create Date: 2026-01-14 00:00:00.000000

"""

from collections.abc import Sequence
import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5024a57ff6e"
down_revision: str | None = "fe20e84914ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM registryaction "
            "WHERE repository_id IN (SELECT id FROM registryrepository WHERE origin = 'custom')"
        )
    )
    op.execute(sa.text("DELETE FROM registryrepository WHERE origin = 'custom'"))


def downgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO registryrepository (id, origin, last_synced_at, commit_sha) "
            "VALUES (:id, 'custom', NULL, NULL) ON CONFLICT (origin) DO NOTHING"
        ),
        {"id": str(uuid.uuid4())},
    )
