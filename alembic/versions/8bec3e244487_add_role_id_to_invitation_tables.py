"""add role_id to invitation tables and drop legacy role columns

Revision ID: 8bec3e244487
Revises: 97812277aa15
Create Date: 2026-02-18 14:43:20.281236

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8bec3e244487"
down_revision: str | None = "97812277aa15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Phase 1: Add role_id to invitation tables ---

    # 1a. Add nullable role_id to organization_invitation
    op.add_column(
        "organization_invitation",
        sa.Column("role_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_organization_invitation_role_id_role"),
        "organization_invitation",
        "role",
        ["role_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 1b. Add nullable role_id to invitation (workspace invitation)
    op.add_column(
        "invitation",
        sa.Column("role_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_invitation_role_id_role"),
        "invitation",
        "role",
        ["role_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 2a. Backfill organization_invitation.role_id from legacy role enum
    op.execute(
        """
        UPDATE organization_invitation AS oi
        SET role_id = r.id
        FROM role AS r
        WHERE r.organization_id = oi.organization_id
          AND r.slug = CASE oi.role::text
              WHEN 'OWNER' THEN 'organization-owner'
              WHEN 'ADMIN' THEN 'organization-admin'
              WHEN 'MEMBER' THEN 'organization-member'
          END
        """
    )

    # 2b. Backfill invitation.role_id from legacy workspace role enum
    op.execute(
        """
        UPDATE invitation AS inv
        SET role_id = r.id
        FROM workspace AS w
        JOIN role AS r ON r.organization_id = w.organization_id
        WHERE w.id = inv.workspace_id
          AND r.slug = CASE inv.role::text
              WHEN 'ADMIN' THEN 'workspace-admin'
              WHEN 'EDITOR' THEN 'workspace-editor'
              WHEN 'VIEWER' THEN 'workspace-viewer'
          END
        """
    )

    bind = op.get_bind()
    org_inv_null_role_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM organization_invitation WHERE role_id IS NULL")
    ).scalar_one()
    inv_null_role_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM invitation WHERE role_id IS NULL")
    ).scalar_one()
    total_null_role_count = org_inv_null_role_count + inv_null_role_count
    if total_null_role_count:
        raise RuntimeError(
            "Migration failed: "
            f"{total_null_role_count} invitations still have NULL role_id "
            f"(organization_invitation={org_inv_null_role_count}, "
            f"invitation={inv_null_role_count}). Ensure all organizations have "
            "seeded preset roles before rerunning this migration."
        )

    # 3. Make role_id NOT NULL after backfill
    op.alter_column("organization_invitation", "role_id", nullable=False)
    op.alter_column("invitation", "role_id", nullable=False)

    # --- Phase 2: Drop legacy role columns ---

    # 4. Drop organization_invitation.role (orgrole enum)
    op.drop_column("organization_invitation", "role")

    # 5. Drop invitation.role (workspacerole enum)
    op.drop_column("invitation", "role")

    # 6. Drop organization_membership.role (orgrole enum)
    op.drop_column("organization_membership", "role")

    # 7. Drop membership.role (workspacerole enum)
    op.drop_column("membership", "role")


def downgrade() -> None:
    # --- Reverse Phase 2: Re-add legacy role columns ---

    # Re-add membership.role with default
    op.add_column(
        "membership",
        sa.Column(
            "role",
            sa.Enum(
                "ADMIN", "EDITOR", "VIEWER", name="workspacerole", create_type=False
            ),
            nullable=False,
            server_default="EDITOR",
        ),
    )
    # Backfill from RBAC assignments
    op.execute(
        """
        UPDATE membership AS m
        SET role = CASE r.slug
            WHEN 'workspace-admin' THEN 'ADMIN'::workspacerole
            WHEN 'workspace-editor' THEN 'EDITOR'::workspacerole
            WHEN 'workspace-viewer' THEN 'VIEWER'::workspacerole
            ELSE 'EDITOR'::workspacerole
        END
        FROM user_role_assignment AS ura
        JOIN role AS r ON r.id = ura.role_id
        WHERE ura.user_id = m.user_id
          AND ura.workspace_id = m.workspace_id
        """
    )

    # Re-add organization_membership.role with default
    op.add_column(
        "organization_membership",
        sa.Column(
            "role",
            sa.Enum("OWNER", "ADMIN", "MEMBER", name="orgrole", create_type=False),
            nullable=False,
            server_default="MEMBER",
        ),
    )
    # Backfill from RBAC assignments
    op.execute(
        """
        UPDATE organization_membership AS om
        SET role = CASE r.slug
            WHEN 'organization-owner' THEN 'OWNER'::orgrole
            WHEN 'organization-admin' THEN 'ADMIN'::orgrole
            WHEN 'organization-member' THEN 'MEMBER'::orgrole
            ELSE 'MEMBER'::orgrole
        END
        FROM user_role_assignment AS ura
        JOIN role AS r ON r.id = ura.role_id
        WHERE ura.user_id = om.user_id
          AND ura.organization_id = om.organization_id
          AND ura.workspace_id IS NULL
        """
    )

    # Re-add invitation.role with default
    op.add_column(
        "invitation",
        sa.Column(
            "role",
            sa.Enum(
                "ADMIN", "EDITOR", "VIEWER", name="workspacerole", create_type=False
            ),
            nullable=False,
            server_default="EDITOR",
        ),
    )
    # Backfill from role_id
    op.execute(
        """
        UPDATE invitation AS inv
        SET role = CASE r.slug
            WHEN 'workspace-admin' THEN 'ADMIN'::workspacerole
            WHEN 'workspace-editor' THEN 'EDITOR'::workspacerole
            WHEN 'workspace-viewer' THEN 'VIEWER'::workspacerole
            ELSE 'EDITOR'::workspacerole
        END
        FROM role AS r
        WHERE r.id = inv.role_id
        """
    )
    op.alter_column("invitation", "role", server_default=None)

    # Re-add organization_invitation.role with default
    op.add_column(
        "organization_invitation",
        sa.Column(
            "role",
            sa.Enum("OWNER", "ADMIN", "MEMBER", name="orgrole", create_type=False),
            nullable=False,
            server_default="MEMBER",
        ),
    )
    # Backfill from role_id
    op.execute(
        """
        UPDATE organization_invitation AS oi
        SET role = CASE r.slug
            WHEN 'organization-owner' THEN 'OWNER'::orgrole
            WHEN 'organization-admin' THEN 'ADMIN'::orgrole
            WHEN 'organization-member' THEN 'MEMBER'::orgrole
            ELSE 'MEMBER'::orgrole
        END
        FROM role AS r
        WHERE r.id = oi.role_id
        """
    )
    op.alter_column("organization_invitation", "role", server_default=None)

    # --- Reverse Phase 1: Drop role_id columns ---
    op.drop_constraint(
        op.f("fk_invitation_role_id_role"), "invitation", type_="foreignkey"
    )
    op.drop_column("invitation", "role_id")
    op.drop_constraint(
        op.f("fk_organization_invitation_role_id_role"),
        "organization_invitation",
        type_="foreignkey",
    )
    op.drop_column("organization_invitation", "role_id")
