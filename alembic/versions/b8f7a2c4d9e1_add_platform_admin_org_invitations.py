"""add platform admin org invitations

Revision ID: b8f7a2c4d9e1
Revises: 0c9a39e54e2f
Create Date: 2026-04-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8f7a2c4d9e1"
down_revision: str | None = "0c9a39e54e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization_invitation",
        sa.Column(
            "created_by_platform_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        """
        UPDATE organization_invitation AS invitation
        SET created_by_platform_admin = true
        FROM "user" AS inviter
        WHERE invitation.invited_by = inviter.id
          AND inviter.is_superuser IS TRUE
        """
    )


def downgrade() -> None:
    op.drop_column("organization_invitation", "created_by_platform_admin")
