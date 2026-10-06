"""Add invitation groups.

Revision ID: 76748dd71c3d
Revises: 6d83f2a91c40
Create Date: 2026-10-05 17:35:42.042389

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import disable_org_table_rls, enable_org_table_rls

revision: str = "76748dd71c3d"
down_revision: str | None = "6d83f2a91c40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invitation_group",
        sa.Column("invitation_id", sa.UUID(), nullable=False),
        sa.Column("group_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["group.id"],
            name=op.f("fk_invitation_group_group_id_group"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"],
            ["invitation.id"],
            name=op.f("fk_invitation_group_invitation_id_invitation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_invitation_group_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "invitation_id", "group_id", name=op.f("pk_invitation_group")
        ),
    )
    op.create_index(
        op.f("ix_invitation_group_group_id"),
        "invitation_group",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invitation_group_organization_id"),
        "invitation_group",
        ["organization_id"],
        unique=False,
    )
    op.execute(enable_org_table_rls("invitation_group"))


def downgrade() -> None:
    op.execute(disable_org_table_rls("invitation_group"))
    op.drop_index(
        op.f("ix_invitation_group_group_id"),
        table_name="invitation_group",
    )
    op.drop_index(
        op.f("ix_invitation_group_organization_id"),
        table_name="invitation_group",
    )
    op.drop_table("invitation_group")
