"""add cascade delete to membership workspace fk.

Revision ID: cd84c08340a5
Revises: b5fc4168fe22
Create Date: 2026-03-11 13:50:36.124118

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cd84c08340a5"
down_revision: str | None = "b5fc4168fe22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_membership_workspace_id_workspace",
        "membership",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_membership_workspace_id_workspace"),
        "membership",
        "workspace",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_membership_workspace_id_workspace"),
        "membership",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_membership_workspace_id_workspace",
        "membership",
        "workspace",
        ["workspace_id"],
        ["id"],
    )
