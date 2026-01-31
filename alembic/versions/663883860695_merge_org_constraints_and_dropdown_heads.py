"""merge org constraints and dropdown heads

Revision ID: 663883860695
Revises: 328d927c631b, 5a3b7c8d9e0f
Create Date: 2026-01-31 17:24:16.047107

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '663883860695'
down_revision: str | None = ('328d927c631b', '5a3b7c8d9e0f')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
