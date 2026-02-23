"""consolidate invitation tables

Revision ID: 1e4dac3e5fe5
Revises: 2410092f4ce4
Create Date: 2026-02-23 14:05:32.219708

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1e4dac3e5fe5"
down_revision: str | None = "2410092f4ce4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add organization_id to invitation (nullable initially for backfill)
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

    # 2. Backfill organization_id from workspace
    op.execute(
        """
        UPDATE invitation
        SET organization_id = workspace.organization_id
        FROM workspace
        WHERE invitation.workspace_id = workspace.id
        """
    )

    # 3. Copy rows from organization_invitation → invitation (workspace_id=NULL)
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
        ON CONFLICT (token) DO NOTHING
        """
    )

    # 4. Make organization_id NOT NULL after backfill
    op.alter_column("invitation", "organization_id", nullable=False)

    # 5. Make workspace_id nullable (org-level invitations have NULL workspace_id)
    op.alter_column(
        "invitation", "workspace_id", existing_type=sa.UUID(), nullable=True
    )

    # 6. Drop old unique constraint, replace with partial unique indexes
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

    # 7. Drop organization_invitation table
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
    # 1. Recreate organization_invitation table
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

    # 2. Copy org-level invitations back to organization_invitation
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

    # 3. Delete org-level rows from invitation
    op.execute("DELETE FROM invitation WHERE workspace_id IS NULL")

    # 4. Drop partial unique indexes
    op.execute("DROP INDEX IF EXISTS uq_invitation_workspace_email")
    op.execute("DROP INDEX IF EXISTS uq_invitation_org_email")

    # 5. Recreate old unique constraint
    op.create_unique_constraint(
        "uq_invitation_workspace_id_email",
        "invitation",
        ["workspace_id", "email"],
    )

    # 6. Make workspace_id NOT NULL again
    op.alter_column(
        "invitation", "workspace_id", existing_type=sa.UUID(), nullable=False
    )

    # 7. Drop organization_id column
    op.drop_constraint(
        op.f("fk_invitation_organization_id_organization"),
        "invitation",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_invitation_organization_id"), table_name="invitation")
    op.drop_column("invitation", "organization_id")
