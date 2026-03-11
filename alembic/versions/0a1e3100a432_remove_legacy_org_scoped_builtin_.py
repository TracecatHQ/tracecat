"""remove legacy org scoped builtin registry repository

Revision ID: 0a1e3100a432
Revises: 6171727be56a
Create Date: 2026-03-11 18:26:36.376624

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0a1e3100a432"
down_revision: str | None = "6171727be56a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Delete legacy org-scoped builtin registry repositories."""
    op.execute(
        sa.text(
            "UPDATE registry_repository "
            "SET current_version_id = NULL "
            "WHERE origin = 'tracecat_registry'"
        )
    )
    op.execute(
        sa.text("DELETE FROM registry_repository WHERE origin = 'tracecat_registry'")
    )


def downgrade() -> None:
    """Legacy data cleanup is not reversible."""
    pass
