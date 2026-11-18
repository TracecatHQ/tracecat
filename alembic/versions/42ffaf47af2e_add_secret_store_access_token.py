"""Store an external secret store's access token on the store, encrypted.

Revision ID: 42ffaf47af2e
Revises: 14588b612098
Create Date: 2026-11-17 12:00:00.000000

Additive: a nullable column. Existing stores (AWS) need no token and keep it null.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "42ffaf47af2e"
down_revision: str | None = "14588b612098"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization_secret_store",
        sa.Column("encrypted_access_token", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organization_secret_store", "encrypted_access_token")
