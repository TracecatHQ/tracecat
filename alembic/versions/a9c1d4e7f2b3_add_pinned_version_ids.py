"""Add pinned version IDs to skills and agent presets.

Revision ID: a9c1d4e7f2b3
Revises: c6a8d4f3b2e1
Create Date: 2026-07-08 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c1d4e7f2b3"
down_revision: str | None = "c6a8d4f3b2e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_preset",
        sa.Column("pinned_version_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_agent_preset_pinned_version_id_agent_preset_version"),
        "agent_preset",
        "agent_preset_version",
        ["pinned_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "skill",
        sa.Column("pinned_version_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_skill_pinned_version_id_skill_version"),
        "skill",
        "skill_version",
        ["pinned_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_skill_pinned_version_id_skill_version"),
        "skill",
        type_="foreignkey",
    )
    op.drop_column("skill", "pinned_version_id")
    op.drop_constraint(
        op.f("fk_agent_preset_pinned_version_id_agent_preset_version"),
        "agent_preset",
        type_="foreignkey",
    )
    op.drop_column("agent_preset", "pinned_version_id")
