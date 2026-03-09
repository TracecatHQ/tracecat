"""add workflow-backed case comments

Revision ID: 286984f514c2
Revises: 3b58a1430e95
Create Date: 2026-03-08 16:50:59.318822

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "286984f514c2"
down_revision: str | None = "3b58a1430e95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("case_comment", sa.Column("workflow_id", sa.UUID(), nullable=True))
    op.add_column(
        "case_comment",
        sa.Column("workflow_title", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "case_comment",
        sa.Column("workflow_alias", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "case_comment",
        sa.Column("workflow_wf_exec_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "case_comment",
        sa.Column("workflow_status", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("case_comment", "workflow_status")
    op.drop_column("case_comment", "workflow_wf_exec_id")
    op.drop_column("case_comment", "workflow_alias")
    op.drop_column("case_comment", "workflow_title")
    op.drop_column("case_comment", "workflow_id")
