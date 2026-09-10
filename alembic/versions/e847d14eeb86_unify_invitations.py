"""unify invitations

Revision ID: e847d14eeb86
Revises: 526f867f6a75
Create Date: 2026-09-10 15:40:00.000000

Makes ``invitation`` the single source of truth for invitations: org-anchored,
with a nullable ``workspace_id`` and a list of grants in ``invitation_grant``.

``organization_invitation`` is deliberately left untouched so the previous app
version keeps working during a rolling deploy. Its pending rows are copied
verbatim into ``invitation``, keeping their ids and tokens so existing accept
links keep working; the contract drop belongs to a later release.

Additive, so the previous app version runs against the upgraded schema; there is
no downgrade because the copied rows cannot be told apart from new ones.

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_org_optional_workspace_table_rls,
    enable_org_optional_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "e847d14eeb86"
down_revision: str | None = "526f867f6a75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invitation_grant",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("invitation_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"],
            ["invitation.id"],
            name=op.f("fk_invitation_grant_invitation_id_invitation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_invitation_grant_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role.id"],
            name=op.f("fk_invitation_grant_role_id_role"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_invitation_grant_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitation_grant")),
    )
    op.create_index(
        op.f("ix_invitation_grant_invitation_id"),
        "invitation_grant",
        ["invitation_id"],
        unique=False,
    )
    op.create_index(
        "ix_invitation_grant_org_unique",
        "invitation_grant",
        ["invitation_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
    )
    op.create_index(
        op.f("ix_invitation_grant_organization_id"),
        "invitation_grant",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invitation_grant_role_id"),
        "invitation_grant",
        ["role_id"],
        unique=False,
    )
    op.create_index(
        "ix_invitation_grant_workspace_unique",
        "invitation_grant",
        ["invitation_id", "workspace_id"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
    )

    op.add_column("invitation", sa.Column("organization_id", sa.UUID(), nullable=True))
    op.add_column(
        "invitation",
        sa.Column(
            "created_by_platform_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE invitation
        SET organization_id = workspace.organization_id
        FROM workspace
        WHERE workspace.id = invitation.workspace_id
          AND invitation.organization_id IS NULL
        """
    )
    # Left nullable: the previous app version inserts workspace invitations
    # without it, so rollback keeps working. New code always sets it.
    op.alter_column(
        "invitation", "workspace_id", existing_type=sa.UUID(), nullable=True
    )
    # Deleting the anchor workspace must not delete the org-anchored invitation.
    op.drop_constraint(
        "fk_invitation_workspace_id_workspace", "invitation", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_invitation_workspace_id_workspace"),
        "invitation",
        "workspace",
        ["workspace_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_invitation_organization_id_organization"),
        "invitation",
        "organization",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_invitation_organization_id"),
        "invitation",
        ["organization_id"],
        unique=False,
    )

    # Every pre-existing workspace invitation becomes one workspace grant.
    op.execute(
        """
        INSERT INTO invitation_grant (
            id, organization_id, invitation_id, workspace_id, role_id
        )
        SELECT gen_random_uuid(),
               invitation.organization_id,
               invitation.id,
               invitation.workspace_id,
               invitation.role_id
        FROM invitation
        WHERE invitation.workspace_id IS NOT NULL
        """
    )

    # Pending org rows are copied verbatim, ids and tokens included, so their
    # existing accept links keep working. Duplicate pending emails are kept:
    # the app, not the database, enforces one pending invitation per email.
    op.execute(
        """
        INSERT INTO invitation (
            id, organization_id, workspace_id, email, status, invited_by,
            role_id, token, expires_at, accepted_at, created_by_platform_admin,
            created_at, updated_at
        )
        SELECT oi.id, oi.organization_id, NULL, oi.email, oi.status,
               oi.invited_by, oi.role_id, oi.token, oi.expires_at,
               oi.accepted_at, oi.created_by_platform_admin,
               oi.created_at, oi.updated_at
        FROM organization_invitation AS oi
        WHERE oi.status = 'PENDING'
        """
    )
    # Each copied row gets its org-wide grant.
    op.execute(
        """
        INSERT INTO invitation_grant (
            id, organization_id, invitation_id, workspace_id, role_id
        )
        SELECT gen_random_uuid(), oi.organization_id, oi.id, NULL, oi.role_id
        FROM organization_invitation AS oi
        WHERE oi.status = 'PENDING'
        """
    )

    # workspace_id is now optional, so the workspace-scoped uniqueness goes.
    op.drop_constraint("uq_invitation_workspace_id_email", "invitation", type_="unique")

    # invitation is now org-anchored with an optional workspace, so its
    # workspace-only policy is replaced.
    op.execute(disable_org_optional_workspace_table_rls("invitation"))
    op.execute(enable_org_optional_workspace_table_rls("invitation"))
    op.execute(enable_org_optional_workspace_table_rls("invitation_grant"))


def downgrade() -> None:
    raise NotImplementedError(
        "e847d14eeb86 copies organization_invitation rows into invitation and cannot "
        "separate them from rows the new app version wrote. This revision is additive: "
        "the previous app version runs against the upgraded schema, so roll the app "
        "back and leave the schema in place. To undo the schema, restore from backup."
    )
