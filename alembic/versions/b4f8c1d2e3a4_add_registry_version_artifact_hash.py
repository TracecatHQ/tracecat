"""Add registry version artifact hash

Revision ID: b4f8c1d2e3a4
Revises: a3d7c9e8b4f2
Create Date: 2026-05-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4f8c1d2e3a4"
down_revision: str | None = "a3d7c9e8b4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "registry_version",
        sa.Column("artifact_hash", sa.String(), nullable=True),
    )
    op.add_column(
        "platform_registry_version",
        sa.Column("artifact_hash", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_registry_version", "artifact_hash")
    op.drop_column("registry_version", "artifact_hash")
