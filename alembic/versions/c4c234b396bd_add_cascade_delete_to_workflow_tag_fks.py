"""add cascade delete to workflow_tag fks

Revision ID: c4c234b396bd
Revises: 5b2c8e91f3a7
Create Date: 2026-01-06 15:40:32.085565

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4c234b396bd"
down_revision: str | None = "5b2c8e91f3a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_workflow_tag_workflow_id_workflow", "workflow_tag", type_="foreignkey"
    )
    op.drop_constraint("fk_workflow_tag_tag_id_tag", "workflow_tag", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk_workflow_tag_workflow_id_workflow"),
        "workflow_tag",
        "workflow",
        ["workflow_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_workflow_tag_tag_id_tag"),
        "workflow_tag",
        "tag",
        ["tag_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_workflow_tag_tag_id_tag"), "workflow_tag", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_workflow_tag_workflow_id_workflow"), "workflow_tag", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_workflow_tag_tag_id_tag", "workflow_tag", "tag", ["tag_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_workflow_tag_workflow_id_workflow",
        "workflow_tag",
        "workflow",
        ["workflow_id"],
        ["id"],
    )
