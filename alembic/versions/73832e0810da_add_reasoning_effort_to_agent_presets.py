"""add reasoning_effort to agent presets

Revision ID: 73832e0810da
Revises: 76748dd71c3d
Create Date: 2026-09-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "73832e0810da"
down_revision: str | None = "76748dd71c3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("agent_preset", "agent_preset_version")


def upgrade() -> None:
    # Expand only: enable_thinking stays and is kept in sync by the app so a
    # rollback keeps each preset's choice. Enabled presets map to NULL (model
    # default); disabled presets map to "off".
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("reasoning_effort", sa.String(length=16), nullable=True),
        )
        op.execute(
            sa.text(
                f"UPDATE {table} SET reasoning_effort = 'off' "
                "WHERE enable_thinking IS FALSE"
            )
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "reasoning_effort")
