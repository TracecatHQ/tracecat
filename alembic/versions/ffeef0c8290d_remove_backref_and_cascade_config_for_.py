"""remove_backref_and_cascade_config_for_v1_simplification

Revision ID: ffeef0c8290d
Revises: 3572800335d3
Create Date: 2025-08-13 20:53:15.322591

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ffeef0c8290d"
down_revision: str | None = "3572800335d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Remove backref and cascade configuration for v1 simplification
    # Relations become unidirectional, cascade delete is always true

    # Drop the foreign key constraint first
    op.drop_constraint(
        "field_metadata_relation_backref_field_id_fkey",
        "field_metadata",
        type_="foreignkey",
    )

    # Remove the backref field column
    op.drop_column("field_metadata", "relation_backref_field_id")

    # Remove the cascade delete configuration column
    # (cascade delete will always be true going forward)
    op.drop_column("field_metadata", "relation_cascade_delete")


def downgrade() -> None:
    # Re-add the cascade delete column with default true
    op.add_column(
        "field_metadata",
        sa.Column(
            "relation_cascade_delete",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )

    # Re-add the backref field column
    op.add_column(
        "field_metadata",
        sa.Column("relation_backref_field_id", postgresql.UUID(), nullable=True),
    )

    # Re-add the foreign key constraint with deferrable option
    op.create_foreign_key(
        "field_metadata_relation_backref_field_id_fkey",
        "field_metadata",
        "field_metadata",
        ["relation_backref_field_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )
