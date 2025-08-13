"""add default_value to field_metadata

Revision ID: 4701058ce39a
Revises: 35612be3b3a5
Create Date: 2025-08-11 22:32:58.064794

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4701058ce39a"
down_revision: str | None = "35612be3b3a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add default_value column to field_metadata table
    op.add_column(
        "field_metadata",
        sa.Column("default_value", JSONB(), nullable=True),
    )


def downgrade() -> None:
    # Remove default_value column from field_metadata table
    op.drop_column("field_metadata", "default_value")
