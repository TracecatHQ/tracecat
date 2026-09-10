"""add invitation email delivery columns

Revision ID: bc3124ad3437
Revises: 526f867f6a75
Create Date: 2026-09-08 15:53:21.646617

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bc3124ad3437"
down_revision: str | None = "526f867f6a75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_organization_invitation_email_unclaimed"


def upgrade() -> None:
    op.add_column(
        "organization_invitation",
        sa.Column("email_claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "organization_invitation",
        sa.Column("email_sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "organization_invitation",
        sa.Column(
            "email_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # Rows predating the outbox were delivered (or not) out of band; claiming
    # them keeps a deploy from mass-emailing every pending invitation.
    op.execute(
        sa.text(
            "UPDATE organization_invitation SET email_claimed_at = now() "
            "WHERE email_claimed_at IS NULL"
        )
    )
    op.create_index(
        INDEX_NAME,
        "organization_invitation",
        ["created_at"],
        # Exhausted and revoked rows keep a NULL claim; keep them out of the scan.
        postgresql_where=sa.text(
            "email_claimed_at IS NULL AND status = 'PENDING' AND email_attempts < 3"
        ),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="organization_invitation")
    op.drop_column("organization_invitation", "email_attempts")
    op.drop_column("organization_invitation", "email_sent_at")
    op.drop_column("organization_invitation", "email_claimed_at")
