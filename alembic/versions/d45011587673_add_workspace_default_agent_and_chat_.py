"""Add workspace default agent and chat visibility.

Revision ID: d45011587673
Revises: 76748dd71c3d
Create Date: 2026-10-07 01:40:37.077312
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d45011587673"
down_revision: str | None = "76748dd71c3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_preset",
        sa.Column(
            "use_in_chat", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.add_column(
        "workspace", sa.Column("default_agent_preset_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        "fk_workspace_default_agent_preset_id_agent_preset",
        "workspace",
        "agent_preset",
        ["default_agent_preset_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_workspace_default_agent_preset_id_agent_preset",
        "workspace",
        type_="foreignkey",
    )
    op.drop_column("workspace", "default_agent_preset_id")
    op.drop_column("agent_preset", "use_in_chat")
