"""add agent preset soft delete

Revision ID: 32b7a1f4d9c2
Revises: 11d479597e08
Create Date: 2026-06-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "32b7a1f4d9c2"
down_revision: str | None = "11d479597e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_preset",
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.drop_constraint(
        "uq_agent_preset_workspace_slug",
        "agent_preset",
        type_="unique",
    )
    op.create_index(
        "uq_agent_preset_workspace_slug_active",
        "agent_preset",
        ["workspace_id", "slug"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Dropping deleted_at would turn every tombstoned preset back into an
    # active one (and reused slugs would violate the full constraint), so
    # refuse while any soft-deleted row exists.
    tombstone = bind.execute(
        sa.text(
            """
            SELECT id
            FROM agent_preset
            WHERE deleted_at IS NOT NULL
            LIMIT 1
            """
        )
    ).first()
    if tombstone is not None:
        raise NotImplementedError(
            "Cannot downgrade while soft-deleted agent_preset rows exist: "
            "dropping deleted_at would resurrect them as active presets. "
            "Hard-delete the tombstoned rows first if downgrade is required."
        )

    op.drop_index(
        "uq_agent_preset_workspace_slug_active",
        table_name="agent_preset",
    )
    op.create_unique_constraint(
        "uq_agent_preset_workspace_slug",
        "agent_preset",
        ["workspace_id", "slug"],
    )
    op.drop_column("agent_preset", "deleted_at")
