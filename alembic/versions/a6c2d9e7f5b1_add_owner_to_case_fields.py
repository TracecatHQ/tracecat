"""Add owner_id to case_fields table."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6c2d9e7f5b1"
down_revision: str | None = "803938b158ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: Add the owner_id column to case_fields table as nullable initially
    # This allows the column to be added without violating NOT NULL constraints
    op.add_column(
        "case_fields",
        sa.Column("owner_id", UUID(as_uuid=True), nullable=True),
        schema="public",
    )

    # Step 2: Create an index on the owner_id column for better query performance
    # This will speed up lookups and joins on the owner_id field
    op.create_index(
        op.f("ix_case_fields_owner_id"),
        "case_fields",
        ["owner_id"],
        unique=False,
        schema="public",
    )

    # Step 3: Populate the new owner_id column with data from the related cases table
    # This copies the owner_id from each case to all its associated case_fields
    # Only copy owner_ids that exist in the workspace table to maintain referential integrity
    op.execute(
        sa.text(
            """
            UPDATE public.case_fields AS cf
            SET owner_id = c.owner_id
            FROM public.cases AS c
            WHERE cf.case_id = c.id
            AND c.owner_id IN (SELECT id FROM public.workspace)
            """
        )
    )

    # Step 4: Delete orphaned case_fields records that don't have a valid owner_id
    # This removes any case_fields that are linked to cases with invalid workspace references
    op.execute(
        sa.text(
            """
            DELETE FROM public.case_fields
            WHERE owner_id IS NULL
            """
        )
    )

    # Step 5: Create a foreign key constraint linking owner_id to workspace.id
    # This ensures referential integrity and cascades deletes from workspace to case_fields
    # This is done AFTER data population to avoid constraint violations
    op.create_foreign_key(
        "fk_case_fields_owner_id",
        "case_fields",
        "workspace",
        ["owner_id"],
        ["id"],
        source_schema="public",
        referent_schema="public",
        ondelete="CASCADE",
    )

    # Step 6: Make the owner_id column NOT NULL after populating it with data
    # This ensures data integrity going forward - all case_fields must have an owner
    op.alter_column(
        "case_fields",
        "owner_id",
        nullable=False,
        schema="public",
    )


def downgrade() -> None:
    # Step 1: Remove the foreign key constraint first (dependencies must be removed before columns)
    op.drop_constraint("fk_case_fields_owner_id", "case_fields", schema="public")

    # Step 2: Remove the index on owner_id column
    op.drop_index(
        op.f("ix_case_fields_owner_id"),
        table_name="case_fields",
        schema="public",
    )

    # Step 3: Finally, drop the owner_id column entirely
    # This removes all owner_id data and the column structure
    op.drop_column("case_fields", "owner_id", schema="public")
