"""add system prompt overrides to agent_custom_provider

Revision ID: c969b5f63428
Revises: d0b32dce7f81
Create Date: 2026-05-08 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c969b5f63428"
down_revision: str | None = "d0b32dce7f81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``system_prompt_replace`` and ``system_prompt_append`` columns to
    ``agent_custom_provider``.

    Both columns are nullable Text, defaulting to NULL on existing rows so
    custom providers created before this migration keep their current
    behaviour (Tracecat default system prompt applied at runtime).
    """
    op.add_column(
        "agent_custom_provider",
        sa.Column("system_prompt_replace", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_custom_provider",
        sa.Column("system_prompt_append", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_custom_provider", "system_prompt_append")
    op.drop_column("agent_custom_provider", "system_prompt_replace")
