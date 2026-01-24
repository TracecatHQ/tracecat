"""Refactor tier id to UUID with slug

Revision ID: b2c3d4e5f678
Revises: a1b2c3d4e5f7
Create Date: 2025-01-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f678"
down_revision: str | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: Add slug column to tier table and copy existing id values
    op.add_column("tier", sa.Column("slug", sa.String(63), nullable=True))
    op.execute("UPDATE tier SET slug = id")
    op.alter_column("tier", "slug", nullable=False)
    op.create_index("ix_tier_slug", "tier", ["slug"], unique=True)

    # Step 2: Add new UUID column for tier.id
    op.add_column(
        "tier", sa.Column("new_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.execute("UPDATE tier SET new_id = gen_random_uuid()")
    op.alter_column("tier", "new_id", nullable=False)

    # Step 3: Add new UUID column for organization_tier.tier_id
    op.add_column(
        "organization_tier",
        sa.Column("new_tier_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Step 4: Update organization_tier.new_tier_id to reference the new UUIDs
    op.execute(
        """
        UPDATE organization_tier ot
        SET new_tier_id = t.new_id
        FROM tier t
        WHERE ot.tier_id = t.id
        """
    )

    # Step 5: Drop the old foreign key constraint
    op.drop_constraint(
        "fk_organization_tier_tier_id_tier", "organization_tier", type_="foreignkey"
    )

    # Step 6: Drop old columns and rename new columns
    # For tier table
    op.drop_constraint("pk_tier", "tier", type_="primary")
    op.drop_column("tier", "id")
    op.alter_column("tier", "new_id", new_column_name="id")
    op.create_primary_key("pk_tier", "tier", ["id"])

    # For organization_tier table
    op.drop_column("organization_tier", "tier_id")
    op.alter_column("organization_tier", "new_tier_id", new_column_name="tier_id")
    op.alter_column("organization_tier", "tier_id", nullable=False)

    # Step 7: Recreate the foreign key constraint
    op.create_foreign_key(
        "fk_organization_tier_tier_id_tier",
        "organization_tier",
        "tier",
        ["tier_id"],
        ["id"],
    )


def downgrade() -> None:
    # Step 1: Add back the old string columns
    op.add_column("tier", sa.Column("old_id", sa.String(), nullable=True))
    op.add_column(
        "organization_tier", sa.Column("old_tier_id", sa.String(), nullable=True)
    )

    # Step 2: Copy slug values back to old_id (they were the original id values)
    op.execute("UPDATE tier SET old_id = slug")

    # Step 3: Update organization_tier.old_tier_id to reference the old string IDs
    op.execute(
        """
        UPDATE organization_tier ot
        SET old_tier_id = t.slug
        FROM tier t
        WHERE ot.tier_id = t.id
        """
    )

    # Step 4: Drop the foreign key constraint
    op.drop_constraint(
        "fk_organization_tier_tier_id_tier", "organization_tier", type_="foreignkey"
    )

    # Step 5: Drop new UUID columns and rename old columns back
    # For tier table
    op.drop_constraint("pk_tier", "tier", type_="primary")
    op.drop_column("tier", "id")
    op.alter_column("tier", "old_id", new_column_name="id")
    op.alter_column("tier", "id", nullable=False)
    op.create_primary_key("pk_tier", "tier", ["id"])

    # For organization_tier table
    op.drop_column("organization_tier", "tier_id")
    op.alter_column("organization_tier", "old_tier_id", new_column_name="tier_id")
    op.alter_column("organization_tier", "tier_id", nullable=False)
    op.execute(
        "ALTER TABLE organization_tier ALTER COLUMN tier_id SET DEFAULT 'default'"
    )

    # Step 6: Drop slug column and index
    op.drop_index("ix_tier_slug", table_name="tier")
    op.drop_column("tier", "slug")

    # Step 7: Recreate the foreign key constraint
    op.create_foreign_key(
        "fk_organization_tier_tier_id_tier",
        "organization_tier",
        "tier",
        ["tier_id"],
        ["id"],
    )
