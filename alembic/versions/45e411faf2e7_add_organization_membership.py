"""Add organization_membership table.

Revision ID: 45e411faf2e7
Revises: c7737fa6338a
Create Date: 2026-01-21 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "45e411faf2e7"
down_revision: str | None = "c7737fa6338a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create the orgrole enum
    orgrole_enum = postgresql.ENUM("member", "admin", "owner", name="orgrole")
    orgrole_enum.create(op.get_bind(), checkfirst=True)

    # Create the organization_membership table
    op.create_table(
        "organization_membership",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(
                "member", "admin", "owner", name="orgrole", create_type=False
            ),
            nullable=False,
            server_default="member",
        ),
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
            ["organization_id"],
            ["organization.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "organization_id"),
    )

    # Create index for org_id lookups
    op.create_index(
        "ix_org_membership_org_id",
        "organization_membership",
        ["organization_id"],
        unique=False,
    )

    # Backfill: Create OrganizationMembership rows for existing users
    # based on their workspace memberships.
    # Users with UserRole.ADMIN -> OrgRole.ADMIN
    # Users with UserRole.BASIC -> OrgRole.MEMBER
    op.execute(
        """
        INSERT INTO organization_membership (user_id, organization_id, role, created_at, updated_at)
        SELECT DISTINCT
            u.id AS user_id,
            w.organization_id,
            CASE
                WHEN u.role = 'ADMIN' THEN 'admin'::orgrole
                ELSE 'member'::orgrole
            END AS role,
            NOW() AS created_at,
            NOW() AS updated_at
        FROM "user" u
        INNER JOIN membership m ON m.user_id = u.id
        INNER JOIN workspace w ON w.id = m.workspace_id
        WHERE NOT u.is_superuser
        ON CONFLICT (user_id, organization_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # Drop the index
    op.drop_index("ix_org_membership_org_id", table_name="organization_membership")

    # Drop the table
    op.drop_table("organization_membership")

    # Drop the enum
    orgrole_enum = postgresql.ENUM("member", "admin", "owner", name="orgrole")
    orgrole_enum.drop(op.get_bind(), checkfirst=True)
