"""merge SAML and case attachments migrations

Revision ID: 06e6cd381615
Revises: 71c8649f752f, 91ddb2827c48
Create Date: 2025-06-05 21:18:21.284127

"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '06e6cd381615'
down_revision: str | tuple[str, ...] | None = ('71c8649f752f', '91ddb2827c48')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
