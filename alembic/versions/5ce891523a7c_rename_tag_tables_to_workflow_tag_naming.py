"""rename tag tables to workflow tag naming

Revision ID: 5ce891523a7c
Revises: 8bec3e244487
Create Date: 2026-02-21 14:27:56.461864

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5ce891523a7c"
down_revision: str | None = "8bec3e244487"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Rename the existing join table out of the way first.
    op.rename_table("workflow_tag", "workflow_tag_link")
    op.execute(
        "ALTER TABLE workflow_tag_link "
        "RENAME CONSTRAINT pk_workflow_tag TO pk_workflow_tag_link"
    )
    op.execute(
        "ALTER TABLE workflow_tag_link "
        "RENAME CONSTRAINT fk_workflow_tag_workflow_id_workflow "
        "TO fk_workflow_tag_link_workflow_id_workflow"
    )
    op.execute(
        "ALTER TABLE workflow_tag_link "
        "RENAME CONSTRAINT fk_workflow_tag_tag_id_tag "
        "TO fk_workflow_tag_link_tag_id_workflow_tag"
    )

    # Rename the workflow tag entity table.
    op.rename_table("tag", "workflow_tag")
    op.execute("ALTER INDEX ix_tag_id RENAME TO ix_workflow_tag_id")
    op.execute("ALTER INDEX ix_tag_name RENAME TO ix_workflow_tag_name")
    op.execute("ALTER INDEX ix_tag_ref RENAME TO ix_workflow_tag_ref")
    op.execute(
        "ALTER TABLE workflow_tag "
        "RENAME CONSTRAINT fk_tag_workspace_id_workspace "
        "TO fk_workflow_tag_workspace_id_workspace"
    )
    op.execute("ALTER TABLE workflow_tag RENAME CONSTRAINT pk_tag TO pk_workflow_tag")


def downgrade() -> None:
    op.execute("ALTER INDEX ix_workflow_tag_id RENAME TO ix_tag_id")
    op.execute("ALTER INDEX ix_workflow_tag_name RENAME TO ix_tag_name")
    op.execute("ALTER INDEX ix_workflow_tag_ref RENAME TO ix_tag_ref")
    op.execute(
        "ALTER TABLE workflow_tag "
        "RENAME CONSTRAINT fk_workflow_tag_workspace_id_workspace "
        "TO fk_tag_workspace_id_workspace"
    )
    op.execute("ALTER TABLE workflow_tag RENAME CONSTRAINT pk_workflow_tag TO pk_tag")
    op.rename_table("workflow_tag", "tag")

    op.execute(
        "ALTER TABLE workflow_tag_link "
        "RENAME CONSTRAINT fk_workflow_tag_link_tag_id_workflow_tag "
        "TO fk_workflow_tag_tag_id_tag"
    )
    op.execute(
        "ALTER TABLE workflow_tag_link "
        "RENAME CONSTRAINT fk_workflow_tag_link_workflow_id_workflow "
        "TO fk_workflow_tag_workflow_id_workflow"
    )
    op.execute(
        "ALTER TABLE workflow_tag_link "
        "RENAME CONSTRAINT pk_workflow_tag_link TO pk_workflow_tag"
    )
    op.rename_table("workflow_tag_link", "workflow_tag")
