"""add workspace foreign key to cases

Revision ID: 3d3c6f1f8c0a
Revises: 3f1bacf5a8a9
Create Date: 2025-11-22 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3d3c6f1f8c0a"
down_revision: str | None = "3f1bacf5a8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

fk_cases_owner_id_workspace = "fk_cases_owner_id_workspace"


def upgrade() -> None:
    # Remove orphaned cases before enforcing the workspace foreign key.
    op.execute(
        """
        DELETE FROM cases
        WHERE owner_id IS NOT NULL
          AND owner_id NOT IN (SELECT id FROM workspace)
          AND EXISTS (SELECT 1 FROM workspace)
        """
    )

    op.create_foreign_key(
        fk_cases_owner_id_workspace,
        "cases",
        "workspace",
        ["owner_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(fk_cases_owner_id_workspace, "cases", type_="foreignkey")
