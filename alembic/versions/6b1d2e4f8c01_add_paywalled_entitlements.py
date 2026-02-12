"""Backfill entitlements for paywalled features on default tier

Revision ID: 6b1d2e4f8c01
Revises: 5a3b7c8d9e0f
Create Date: 2026-02-04 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6b1d2e4f8c01"
down_revision: str | None = "2e1f96db2255"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE tier
        SET entitlements = COALESCE(entitlements, '{}'::jsonb) ||
            '{"agent_addons": true, "case_addons": true}'::jsonb
        WHERE is_default = true
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE tier
        SET entitlements = (
            COALESCE(entitlements, '{}'::jsonb)
            - 'agent_addons'
            - 'case_addons'
        )
        WHERE is_default = true
        """
    )
