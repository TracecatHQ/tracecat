"""add tier to organization

Revision ID: c1a2b3d4e5f6
Revises: bf73d15eca35
Create Date: 2026-01-22

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1a2b3d4e5f6"
down_revision: str | None = "bf73d15eca35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization",
        sa.Column("tier", sa.String(), nullable=False, server_default="starter"),
    )
    op.create_index(
        op.f("ix_organization_tier"), "organization", ["tier"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_organization_tier"), table_name="organization")
    op.drop_column("organization", "tier")
