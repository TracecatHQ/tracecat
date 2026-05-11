"""Add agent preset folder lookup index

Revision ID: a3d7c9e8b4f2
Revises: 9f0b5f6a2d1c
Create Date: 2026-05-11 18:12:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3d7c9e8b4f2"
down_revision: str | None = "9f0b5f6a2d1c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_agent_preset_workspace_folder",
        "agent_preset",
        ["workspace_id", "folder_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_preset_workspace_folder", table_name="agent_preset")
