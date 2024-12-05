"""Add accesstoken id

Revision ID: b7a3a2146bac
Revises: 307c3bb9fad5
Create Date: 2024-12-04 13:00:57.251229

"""
# Import uuid for generating IDs
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7a3a2146bac"
down_revision: str | None = "307c3bb9fad5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. First add the column as nullable
    op.add_column("accesstoken", sa.Column("id", UUID(as_uuid=True), nullable=True))

    # 2. Update existing records with new UUIDs
    tbl = sa.table("accesstoken", sa.column("id", sa.UUID))
    op.execute(
        tbl.update().where(tbl.c.id.is_(None)).values(id=sa.func.gen_random_uuid())
    )

    # 3. Make the column non-nullable and unique
    op.alter_column(
        "accesstoken", "id", existing_type=UUID(as_uuid=True), nullable=False
    )

    op.create_unique_constraint("uq_accesstoken_id", "accesstoken", ["id"])


def downgrade() -> None:
    op.drop_constraint("uq_accesstoken_id", "accesstoken")
    op.drop_column("accesstoken", "id")
