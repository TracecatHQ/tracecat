"""add webhook include_headers

Revision ID: c9d2e7a4f1b3
Revises: 0f18bea0c115
Create Date: 2026-06-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d2e7a4f1b3"
down_revision: str | None = "0f18bea0c115"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "webhook",
        sa.Column(
            "include_headers",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("webhook", "include_headers")
