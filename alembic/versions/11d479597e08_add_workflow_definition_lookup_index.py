"""add workflow definition lookup index

Revision ID: 11d479597e08
Revises: e32940d12293
Create Date: 2026-07-04 22:02:13.128868

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "11d479597e08"
down_revision: str | None = "e32940d12293"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_workflow_definition_workspace_id_workflow_id_version",
        "workflow_definition",
        ["workspace_id", "workflow_id", "version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_definition_workspace_id_workflow_id_version",
        table_name="workflow_definition",
    )
