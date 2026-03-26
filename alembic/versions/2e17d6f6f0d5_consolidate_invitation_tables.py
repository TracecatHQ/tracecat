"""consolidate invitation tables

Revision ID: 2e17d6f6f0d5
Revises: 0a1e3100a432
Create Date: 2026-03-12 16:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    enable_org_optional_workspace_table_rls,
    enable_workspace_table_rls,
    policy_name,
)

# revision identifiers, used by Alembic.
revision: str = "2e17d6f6f0d5"
down_revision: str | None = "0a1e3100a432"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE t.typname = 'invitationstatus'
                  AND e.enumlabel = 'declined'
            ) THEN
                ALTER TYPE invitationstatus RENAME VALUE 'declined' TO 'DECLINED';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE t.typname = 'invitationstatus'
                  AND e.enumlabel = 'DECLINED'
            ) THEN
                ALTER TYPE invitationstatus ADD VALUE 'DECLINED';
            END IF;
        END
        $$;
        """
    )

    # 1. Add organization_id to workspace invitations (nullable initially for backfill).
    op.add_column("invitation", sa.Column("organization_id", sa.UUID(), nullable=True))
    op.create_index(
        op.f("ix_invitation_organization_id"),
        "invitation",
        ["organization_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_invitation_organization_id_organization"),
        "invitation",
        "organization",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 2. Backfill organization_id from the workspace owner organization.
    op.execute(
        """
        UPDATE invitation
        SET organization_id = workspace.organization_id
        FROM workspace
        WHERE invitation.workspace_id = workspace.id
        """
    )

    # 3. Make workspace_id nullable before inserting organization-level rows.
    op.alter_column(
        "invitation",
        "workspace_id",
        existing_type=sa.UUID(),
        nullable=True,
    )

    legacy_org_invitation_count = bind.execute(
        sa.text("SELECT count(*) FROM organization_invitation")
    ).scalar_one()
    token_collision_count = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM organization_invitation oi
            JOIN invitation i ON i.token = oi.token
            """
        )
    ).scalar_one()
    id_collision_count = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM organization_invitation oi
            JOIN invitation i ON i.id = oi.id
            """
        )
    ).scalar_one()
    org_email_collision_count = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM organization_invitation oi
            JOIN invitation i
              ON i.workspace_id IS NULL
             AND i.organization_id = oi.organization_id
             AND lower(i.email) = lower(oi.email)
            """
        )
    ).scalar_one()
    if token_collision_count or id_collision_count or org_email_collision_count:
        raise RuntimeError(
            "Invitation consolidation aborted due to conflicts: "
            f"token_collisions={token_collision_count}, "
            f"id_collisions={id_collision_count}, "
            f"org_email_collisions={org_email_collision_count}. "
            "Resolve collisions before rerunning this migration."
        )

    # 4. Copy organization invitations into the unified invitation table.
    op.execute(
        """
        INSERT INTO invitation (
            id, organization_id, workspace_id, email, status, invited_by,
            role_id, token, expires_at, accepted_at, created_at, updated_at
        )
        SELECT
            id, organization_id, NULL, email, status, invited_by,
            role_id, token, expires_at, accepted_at, created_at, updated_at
        FROM organization_invitation
        """
    )
    migrated_org_invitation_count = bind.execute(
        sa.text("SELECT count(*) FROM invitation WHERE workspace_id IS NULL")
    ).scalar_one()
    if migrated_org_invitation_count != legacy_org_invitation_count:
        raise RuntimeError(
            "Invitation consolidation copy verification failed: "
            f"expected={legacy_org_invitation_count}, "
            f"actual={migrated_org_invitation_count} org-level invitations. "
            "Refusing to drop organization_invitation."
        )

    # 5. Make organization_id required now that all rows are backfilled.
    op.alter_column("invitation", "organization_id", nullable=False)
    op.execute(f'DROP POLICY IF EXISTS {policy_name("invitation")} ON "invitation"')
    op.execute(enable_org_optional_workspace_table_rls("invitation"))

    # 6. Replace the old workspace unique constraint with partial unique indexes.
    op.drop_constraint("uq_invitation_workspace_id_email", "invitation", type_="unique")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_invitation_workspace_email
        ON invitation(workspace_id, email)
        WHERE workspace_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_invitation_org_email
        ON invitation(organization_id, email)
        WHERE workspace_id IS NULL
        """
    )

    # 7. Drop the legacy organization invitation table.
    op.drop_index(
        "ix_organization_invitation_organization_id",
        table_name="organization_invitation",
    )
    op.drop_index(
        "ix_organization_invitation_status",
        table_name="organization_invitation",
    )
    op.drop_table("organization_invitation")


def downgrade() -> None:
    # NOTE: PostgreSQL enum values cannot be removed safely here, so the
    # invitationstatus type will retain 'DECLINED' after downgrade.

    op.create_table(
        "organization_invitation",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("organization_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("email", sa.VARCHAR(length=255), autoincrement=False, nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "ACCEPTED",
                "REVOKED",
                "DECLINED",
                name="invitationstatus",
                create_type=False,
            ),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("invited_by", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column("token", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column(
            "expires_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "accepted_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("role_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["user.id"],
            name="fk_organization_invitation_invited_by_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_organization_invitation_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role.id"],
            name="fk_organization_invitation_role_id_role",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_invitation"),
        sa.UniqueConstraint(
            "email",
            "organization_id",
            name="uq_organization_invitation_email_organization_id",
        ),
        sa.UniqueConstraint("token", name="uq_organization_invitation_token"),
    )
    op.create_index(
        "ix_organization_invitation_status",
        "organization_invitation",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_organization_invitation_organization_id",
        "organization_invitation",
        ["organization_id"],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO organization_invitation (
            id, organization_id, email, status, invited_by,
            role_id, token, expires_at, accepted_at, created_at, updated_at
        )
        SELECT
            id, organization_id, email, status, invited_by,
            role_id, token, expires_at, accepted_at, created_at, updated_at
        FROM invitation
        WHERE workspace_id IS NULL
        """
    )

    op.execute("DELETE FROM invitation WHERE workspace_id IS NULL")
    op.execute("DROP INDEX IF EXISTS uq_invitation_workspace_email")
    op.execute("DROP INDEX IF EXISTS uq_invitation_org_email")
    op.execute(f'DROP POLICY IF EXISTS {policy_name("invitation")} ON "invitation"')
    op.execute(enable_workspace_table_rls("invitation"))
    op.create_unique_constraint(
        "uq_invitation_workspace_id_email",
        "invitation",
        ["workspace_id", "email"],
    )
    op.alter_column(
        "invitation",
        "workspace_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
    op.drop_constraint(
        op.f("fk_invitation_organization_id_organization"),
        "invitation",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_invitation_organization_id"), table_name="invitation")
    op.drop_column("invitation", "organization_id")
