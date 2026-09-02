"""add max_output_tokens to agent_custom_provider

Revision ID: 3f8c2a9d1e57
Revises: 44d7e75b6f4c
Create Date: 2026-09-02 15:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f8c2a9d1e57"
down_revision: str | None = "44d7e75b6f4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_custom_provider",
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_custom_provider", "max_output_tokens")
