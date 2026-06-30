"""add agent preset archive timestamp

Revision ID: 32b7a1f4d9c2
Revises: 31b8cb7b312e
Create Date: 2026-06-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "32b7a1f4d9c2"
down_revision: str | None = "31b8cb7b312e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_preset",
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.drop_constraint(
        "uq_agent_preset_workspace_slug",
        "agent_preset",
        type_="unique",
    )
    op.create_index(
        "uq_agent_preset_workspace_slug",
        "agent_preset",
        ["workspace_id", "slug"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade is unsafe after archived preset slug reuse. Restore the database "
        "from backup or snapshot before rolling the application back."
    )
