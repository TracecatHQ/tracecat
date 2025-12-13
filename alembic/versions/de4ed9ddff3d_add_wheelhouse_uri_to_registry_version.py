"""add wheelhouse_uri to registry_version

Revision ID: de4ed9ddff3d
Revises: 74a426bea776
Create Date: 2025-12-11 16:23:05.354915

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "de4ed9ddff3d"
down_revision: str | None = "74a426bea776"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "registry_version", sa.Column("wheelhouse_uri", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("registry_version", "wheelhouse_uri")
