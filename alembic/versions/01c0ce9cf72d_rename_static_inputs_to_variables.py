"""Rename static_inputs to variables

Revision ID: 01c0ce9cf72d
Revises: 046d417c113f
Create Date: 2024-11-08 18:18:54.464298

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "01c0ce9cf72d"
down_revision: str | None = "046d417c113f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add new column
    op.add_column(
        "workflow",
        sa.Column("variables", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # Transfer existing data from static_inputs to variables
    op.execute("UPDATE workflow SET variables = static_inputs")

    # Update WorkflowDefinition content field
    op.execute(
        """
        UPDATE workflowdefinition
        SET content = jsonb_set(
            content,
            '{variables}',
            content->'inputs'
        ) - 'inputs'
        WHERE content ? 'inputs'
    """
    )

    # Drop old column
    op.drop_column("workflow", "static_inputs")


def downgrade() -> None:
    # Add old column back
    op.add_column(
        "workflow",
        sa.Column(
            "static_inputs",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=True,
        ),
    )

    # Transfer data back from variables to static_inputs
    op.execute("UPDATE workflow SET static_inputs = variables")

    # Revert WorkflowDefinition content field changes
    op.execute(
        """
        UPDATE workflowdefinition
        SET content = jsonb_set(
            content,
            '{inputs}',
            content->'variables'
        ) - 'variables'
        WHERE content ? 'variables'
    """
    )

    # Drop new column
    op.drop_column("workflow", "variables")
    # ### end Alembic commands ###
