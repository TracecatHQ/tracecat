"""add entity constraint indexes

Revision ID: 35612be3b3a5
Revises: 2c78f74aa040
Create Date: 2025-08-07 22:24:22.452732

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "35612be3b3a5"
down_revision: str | None = "2c78f74aa040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add index for required field checks (null detection)
    # Note: idx_entity_data_gin already exists for general GIN indexing
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_entity_data_null_fields
        ON entity_data USING gin(field_data jsonb_path_ops)
        WHERE owner_id IS NOT NULL
    """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_entity_data_null_fields")
