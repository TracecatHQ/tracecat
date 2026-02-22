"""rename workflow tag unique constraints

Revision ID: 2410092f4ce4
Revises: 5ce891523a7c
Create Date: 2026-02-21 20:39:57.189601

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2410092f4ce4"
down_revision: str | None = "5ce891523a7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workflow_tag "
        "RENAME CONSTRAINT uq_tag_name_workspace "
        "TO uq_workflow_tag_name_workspace"
    )
    op.execute(
        "ALTER TABLE workflow_tag "
        "RENAME CONSTRAINT uq_tag_ref_workspace "
        "TO uq_workflow_tag_ref_workspace"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE workflow_tag "
        "RENAME CONSTRAINT uq_workflow_tag_name_workspace "
        "TO uq_tag_name_workspace"
    )
    op.execute(
        "ALTER TABLE workflow_tag "
        "RENAME CONSTRAINT uq_workflow_tag_ref_workspace "
        "TO uq_tag_ref_workspace"
    )
