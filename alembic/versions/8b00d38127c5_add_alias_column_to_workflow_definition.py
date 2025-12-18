"""add alias column to workflow_definition

Revision ID: 8b00d38127c5
Revises: 9079ae19374b
Create Date: 2025-12-15 14:48:11.880306

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b00d38127c5"
down_revision: str | None = "9079ae19374b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add alias column to workflow_definition
    op.add_column("workflow_definition", sa.Column("alias", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_workflow_definition_alias"),
        "workflow_definition",
        ["alias"],
        unique=False,
    )

    # Backfill existing workflow definitions with their workflow's current alias
    # For each workflow_definition, copy the alias from the associated workflow
    op.execute(
        """
        UPDATE workflow_definition wd
        SET alias = w.alias
        FROM workflow w
        WHERE wd.workflow_id = w.id
        AND w.alias IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_workflow_definition_alias"), table_name="workflow_definition"
    )
    op.drop_column("workflow_definition", "alias")
