"""remove stale org auth settings

Revision ID: 2e1f96db2255
Revises: 103eb05cde37
Create Date: 2026-02-09 17:49:14.962642

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2e1f96db2255"
down_revision: str | None = "103eb05cde37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM organization_setting
            WHERE key IN (
                'auth_basic_enabled',
                'auth_require_email_verification',
                'auth_allowed_email_domains',
                'auth_min_password_length',
                'auth_session_expire_time_seconds',
                'oauth_google_enabled'
            )
            """
        )
    )


def downgrade() -> None:
    # Data-only cleanup migration is not reversible.
    return None
