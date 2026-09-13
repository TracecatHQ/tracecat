"""Preserve multi-workspace access for existing tiers.

Revision ID: 31ee4b7f175a
Revises: 526f867f6a75
Create Date: 2026-09-12
"""

import sqlalchemy as sa

from alembic import op

revision: str = "31ee4b7f175a"
down_revision: str | None = "526f867f6a75"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Existing hosted tiers retain their workspace creation behavior. OSS uses
    # static entitlements and remains limited regardless of these stored values.
    # Only rows without the key are touched, so an explicit true/false already
    # stored for this entitlement is preserved. `entitlements` is NOT NULL.
    op.execute(
        sa.text(
            """
            UPDATE tier
            SET entitlements = jsonb_set(
                entitlements,
                '{multi_workspace}',
                'true'::jsonb,
                true  -- create_if_missing
            )
            WHERE NOT (entitlements ? 'multi_workspace')
            """
        )
    )


def downgrade() -> None:
    # Keep entitlement data: removing the key could discard an explicit setting,
    # and older application versions safely ignore it.
    pass
