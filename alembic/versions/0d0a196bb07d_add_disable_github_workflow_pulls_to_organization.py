"""add disable_github_workflow_pulls to organization

Revision ID: 0d0a196bb07d
Revises: 96470fdcc686
Create Date: 2026-04-29 20:13:22.674875

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0d0a196bb07d"
down_revision: str | None = "96470fdcc686"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization",
        sa.Column(
            "disable_github_workflow_pulls",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("organization", "disable_github_workflow_pulls")
