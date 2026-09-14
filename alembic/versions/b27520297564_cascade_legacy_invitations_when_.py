"""Cascade legacy invitations when deleting roles.

Revision ID: b27520297564
Revises: 31ee4b7f175a
Create Date: 2026-09-14 11:13:40.211636

The retained legacy table must not prevent deletion of an unused custom role.
Deleting a role retires its old invitations as well as its new grants. Upgrade
changes no existing rows. Downgrade restores RESTRICT for future deletions;
invitations deliberately deleted with a role are not resurrected.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b27520297564"
down_revision: str | None = "31ee4b7f175a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "fk_organization_invitation_role_id_role"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "organization_invitation", type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT,
        "organization_invitation",
        "role",
        ["role_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "organization_invitation", type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT,
        "organization_invitation",
        "role",
        ["role_id"],
        ["id"],
        ondelete="RESTRICT",
    )
