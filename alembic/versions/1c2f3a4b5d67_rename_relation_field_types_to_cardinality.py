"""rename relation field types to cardinality terms

Revision ID: 1c2f3a4b5d67
Revises: 2055f3d2f021
Create Date: 2025-08-23 00:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "1c2f3a4b5d67"
down_revision = "2055f3d2f021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Update stored field_type values in field_metadata
    op.execute(
        """
        UPDATE field_metadata
        SET field_type = 'RELATION_ONE_TO_ONE'
        WHERE field_type = 'RELATION_BELONGS_TO';
        """
    )
    op.execute(
        """
        UPDATE field_metadata
        SET field_type = 'RELATION_ONE_TO_MANY'
        WHERE field_type = 'RELATION_HAS_MANY';
        """
    )


def downgrade() -> None:
    # Revert field_type values back to previous names
    op.execute(
        """
        UPDATE field_metadata
        SET field_type = 'RELATION_BELONGS_TO'
        WHERE field_type = 'RELATION_ONE_TO_ONE';
        """
    )
    op.execute(
        """
        UPDATE field_metadata
        SET field_type = 'RELATION_HAS_MANY'
        WHERE field_type = 'RELATION_ONE_TO_MANY';
        """
    )
