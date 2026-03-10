"""drop case comment workflow foreign key

Revision ID: 9a6d0e0ec5b1
Revises: 286984f514c2
Create Date: 2026-03-08 22:20:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a6d0e0ec5b1"
down_revision: str | None = "286984f514c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKFLOW_FK = "fk_case_comment_workflow_id_workflow"


def upgrade() -> None:
    op.drop_constraint(WORKFLOW_FK, "case_comment", type_="foreignkey")


def downgrade() -> None:
    op.create_foreign_key(
        WORKFLOW_FK,
        "case_comment",
        "workflow",
        ["workflow_id"],
        ["id"],
        ondelete="SET NULL",
    )
