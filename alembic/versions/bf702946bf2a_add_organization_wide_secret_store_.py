"""Add organization-wide secret store access.

Revision ID: bf702946bf2a
Revises: a7f3e2c9d1b4
Create Date: 2026-09-21 15:30:08.155717
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "bf702946bf2a"
down_revision: str | None = "a7f3e2c9d1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization_secret_store",
        sa.Column(
            "all_workspaces",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("organization_secret_store", "all_workspaces")
