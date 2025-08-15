"""rename_entity_metadata_to_entity_and_entity_data_to_record

Revision ID: 9e9f2a7ae56d
Revises: 693d46fcd203
Create Date: 2025-08-14 16:45:13.252413

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = '9e9f2a7ae56d'
down_revision: str | None = '693d46fcd203'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename entity_metadata to entity and entity_data to record across the schema."""

    # 1. Rename tables
    op.rename_table("entity_metadata", "entity")
    op.rename_table("entity_data", "record")
    op.rename_table("entity_relation_link", "record_relation_link")
    op.rename_table("case_entity_link", "case_record_link")

    # 2. Rename columns in field_metadata table
    op.alter_column(
        "field_metadata",
        "entity_metadata_id",
        new_column_name="entity_id",
        existing_type=sa.UUID(),
        existing_nullable=True,
    )
    op.alter_column(
        "field_metadata",
        "relation_target_entity_id",
        new_column_name="target_entity_id",
        existing_type=sa.UUID(),
        existing_nullable=True,
    )

    # 3. Rename columns in record table (formerly entity_data)
    op.alter_column(
        "record",
        "entity_metadata_id",
        new_column_name="entity_id",
        existing_type=sa.UUID(),
        existing_nullable=True,
    )

    # 4. Rename columns in record_relation_link table (formerly entity_relation_link)
    op.alter_column(
        "record_relation_link",
        "source_entity_metadata_id",
        new_column_name="source_entity_id",
        existing_type=sa.UUID(),
        existing_nullable=True,
    )
    op.alter_column(
        "record_relation_link",
        "target_entity_metadata_id",
        new_column_name="target_entity_id",
        existing_type=sa.UUID(),
        existing_nullable=True,
    )

    # 5. Rename columns in case_record_link table (formerly case_entity_link)
    op.alter_column(
        "case_record_link",
        "entity_metadata_id",
        new_column_name="entity_id",
        existing_type=sa.UUID(),
        existing_nullable=True,
    )
    op.alter_column(
        "case_record_link",
        "entity_data_id",
        new_column_name="record_id",
        existing_type=sa.UUID(),
        existing_nullable=True,
    )

    # 6. Drop old constraints and indexes (they'll be recreated with new names)

    # Drop constraints
    op.drop_constraint("field_metadata_entity_metadata_id_field_key_key", "field_metadata", type_="unique")
    op.drop_constraint("uq_entity_relation_link_triple", "record_relation_link", type_="unique")
    op.drop_constraint("uq_case_entity_link", "case_record_link", type_="unique")

    # Drop indexes
    op.drop_index("idx_entity_data_gin", "record")
    op.drop_index("idx_entity_owner_created", "record")
    op.drop_index("idx_entity_metadata_id", "record")
    op.drop_index("idx_relation_source", "record_relation_link")
    op.drop_index("idx_relation_target", "record_relation_link")
    op.drop_index("idx_relation_owner", "record_relation_link")
    op.drop_index("idx_relation_field_target", "record_relation_link")
    op.drop_index("idx_case_entity_case", "case_record_link")
    op.drop_index("idx_case_entity_metadata", "case_record_link")
    op.drop_index("idx_active_fields", "field_metadata")

    # 7. Create new constraints and indexes with updated names

    # Recreate unique constraints
    op.create_unique_constraint(
        "field_metadata_entity_id_field_key_key",
        "field_metadata",
        ["entity_id", "field_key"]
    )
    op.create_unique_constraint(
        "uq_record_relation_link_triple",
        "record_relation_link",
        ["source_record_id", "source_field_id", "target_record_id"]
    )
    op.create_unique_constraint(
        "uq_case_record_link",
        "case_record_link",
        ["case_id", "record_id"]
    )

    # Recreate indexes with new names
    op.create_index("idx_record_gin", "record", ["field_data"], postgresql_using="gin")
    op.create_index("idx_record_owner_created", "record", ["entity_id", "owner_id", "created_at"])
    op.create_index("idx_record_entity_id", "record", ["entity_id"])
    op.create_index("idx_record_relation_source", "record_relation_link", ["source_record_id", "source_field_id"])
    op.create_index("idx_record_relation_target", "record_relation_link", ["target_record_id"])
    op.create_index("idx_record_relation_owner", "record_relation_link", ["owner_id"])
    op.create_index("idx_record_relation_field_target", "record_relation_link", ["source_field_id", "target_record_id"])
    op.create_index("idx_case_record_case", "case_record_link", ["case_id"])
    op.create_index("idx_case_record_entity", "case_record_link", ["entity_id"])
    op.create_index("idx_active_fields", "field_metadata", ["entity_id", "is_active"])

    # 8. Update foreign key constraints
    # Drop old FKs
    op.drop_constraint("field_metadata_entity_metadata_id_fkey", "field_metadata", type_="foreignkey")
    op.drop_constraint("field_metadata_relation_target_entity_id_fkey", "field_metadata", type_="foreignkey")
    op.drop_constraint("entity_data_entity_metadata_id_fkey", "record", type_="foreignkey")
    op.drop_constraint("entity_relation_link_source_entity_metadata_id_fkey", "record_relation_link", type_="foreignkey")
    op.drop_constraint("entity_relation_link_target_entity_metadata_id_fkey", "record_relation_link", type_="foreignkey")
    op.drop_constraint("entity_relation_link_source_record_id_fkey", "record_relation_link", type_="foreignkey")
    op.drop_constraint("entity_relation_link_target_record_id_fkey", "record_relation_link", type_="foreignkey")
    op.drop_constraint("entity_relation_link_source_field_id_fkey", "record_relation_link", type_="foreignkey")
    op.drop_constraint("case_entity_link_case_id_fkey", "case_record_link", type_="foreignkey")
    op.drop_constraint("case_entity_link_entity_metadata_id_fkey", "case_record_link", type_="foreignkey")
    op.drop_constraint("case_entity_link_entity_data_id_fkey", "case_record_link", type_="foreignkey")

    # Create new FKs with updated names
    op.create_foreign_key(
        "field_metadata_entity_id_fkey",
        "field_metadata", "entity",
        ["entity_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "field_metadata_target_entity_id_fkey",
        "field_metadata", "entity",
        ["target_entity_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "record_entity_id_fkey",
        "record", "entity",
        ["entity_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "record_relation_link_source_entity_id_fkey",
        "record_relation_link", "entity",
        ["source_entity_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "record_relation_link_target_entity_id_fkey",
        "record_relation_link", "entity",
        ["target_entity_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "record_relation_link_source_record_id_fkey",
        "record_relation_link", "record",
        ["source_record_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "record_relation_link_target_record_id_fkey",
        "record_relation_link", "record",
        ["target_record_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "record_relation_link_source_field_id_fkey",
        "record_relation_link", "field_metadata",
        ["source_field_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "case_record_link_case_id_fkey",
        "case_record_link", "cases",
        ["case_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "case_record_link_entity_id_fkey",
        "case_record_link", "entity",
        ["entity_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "case_record_link_record_id_fkey",
        "case_record_link", "record",
        ["record_id"], ["id"],
        ondelete="CASCADE"
    )


def downgrade() -> None:
    """Revert the rename from entity/record back to entity_metadata/entity_data."""

    # Drop new FKs
    op.drop_constraint("field_metadata_entity_id_fkey", "field_metadata", type_="foreignkey")
    op.drop_constraint("field_metadata_target_entity_id_fkey", "field_metadata", type_="foreignkey")
    op.drop_constraint("record_entity_id_fkey", "record", type_="foreignkey")
    op.drop_constraint("record_relation_link_source_entity_id_fkey", "record_relation_link", type_="foreignkey")
    op.drop_constraint("record_relation_link_target_entity_id_fkey", "record_relation_link", type_="foreignkey")
    op.drop_constraint("record_relation_link_source_record_id_fkey", "record_relation_link", type_="foreignkey")
    op.drop_constraint("record_relation_link_target_record_id_fkey", "record_relation_link", type_="foreignkey")
    op.drop_constraint("record_relation_link_source_field_id_fkey", "record_relation_link", type_="foreignkey")
    op.drop_constraint("case_record_link_case_id_fkey", "case_record_link", type_="foreignkey")
    op.drop_constraint("case_record_link_entity_id_fkey", "case_record_link", type_="foreignkey")
    op.drop_constraint("case_record_link_record_id_fkey", "case_record_link", type_="foreignkey")

    # Drop new indexes
    op.drop_index("idx_record_gin", "record")
    op.drop_index("idx_record_owner_created", "record")
    op.drop_index("idx_record_entity_id", "record")
    op.drop_index("idx_record_relation_source", "record_relation_link")
    op.drop_index("idx_record_relation_target", "record_relation_link")
    op.drop_index("idx_record_relation_owner", "record_relation_link")
    op.drop_index("idx_record_relation_field_target", "record_relation_link")
    op.drop_index("idx_case_record_case", "case_record_link")
    op.drop_index("idx_case_record_entity", "case_record_link")
    op.drop_index("idx_active_fields", "field_metadata")

    # Drop new constraints
    op.drop_constraint("field_metadata_entity_id_field_key_key", "field_metadata", type_="unique")
    op.drop_constraint("uq_record_relation_link_triple", "record_relation_link", type_="unique")
    op.drop_constraint("uq_case_record_link", "case_record_link", type_="unique")

    # Rename columns back
    op.alter_column("case_record_link", "record_id", new_column_name="entity_data_id")
    op.alter_column("case_record_link", "entity_id", new_column_name="entity_metadata_id")
    op.alter_column("record_relation_link", "target_entity_id", new_column_name="target_entity_metadata_id")
    op.alter_column("record_relation_link", "source_entity_id", new_column_name="source_entity_metadata_id")
    op.alter_column("record", "entity_id", new_column_name="entity_metadata_id")
    op.alter_column("field_metadata", "target_entity_id", new_column_name="relation_target_entity_id")
    op.alter_column("field_metadata", "entity_id", new_column_name="entity_metadata_id")

    # Rename tables back
    op.rename_table("case_record_link", "case_entity_link")
    op.rename_table("record_relation_link", "entity_relation_link")
    op.rename_table("record", "entity_data")
    op.rename_table("entity", "entity_metadata")

    # Recreate original constraints and indexes
    op.create_unique_constraint(
        "field_metadata_entity_metadata_id_field_key_key",
        "field_metadata",
        ["entity_metadata_id", "field_key"]
    )
    op.create_unique_constraint(
        "uq_entity_relation_link_triple",
        "entity_relation_link",
        ["source_record_id", "source_field_id", "target_record_id"]
    )
    op.create_unique_constraint(
        "uq_case_entity_link",
        "case_entity_link",
        ["case_id", "entity_data_id"]
    )

    op.create_index("idx_entity_data_gin", "entity_data", ["field_data"], postgresql_using="gin")
    op.create_index("idx_entity_owner_created", "entity_data", ["entity_metadata_id", "owner_id", "created_at"])
    op.create_index("idx_entity_metadata_id", "entity_data", ["entity_metadata_id"])
    op.create_index("idx_relation_source", "entity_relation_link", ["source_record_id", "source_field_id"])
    op.create_index("idx_relation_target", "entity_relation_link", ["target_record_id"])
    op.create_index("idx_relation_owner", "entity_relation_link", ["owner_id"])
    op.create_index("idx_relation_field_target", "entity_relation_link", ["source_field_id", "target_record_id"])
    op.create_index("idx_case_entity_case", "case_entity_link", ["case_id"])
    op.create_index("idx_case_entity_metadata", "case_entity_link", ["entity_metadata_id"])
    op.create_index("idx_active_fields", "field_metadata", ["entity_metadata_id", "is_active"])

    # Recreate original FKs
    op.create_foreign_key(
        "field_metadata_entity_metadata_id_fkey",
        "field_metadata", "entity_metadata",
        ["entity_metadata_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "field_metadata_relation_target_entity_id_fkey",
        "field_metadata", "entity_metadata",
        ["relation_target_entity_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "entity_data_entity_metadata_id_fkey",
        "entity_data", "entity_metadata",
        ["entity_metadata_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "entity_relation_link_source_entity_metadata_id_fkey",
        "entity_relation_link", "entity_metadata",
        ["source_entity_metadata_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "entity_relation_link_target_entity_metadata_id_fkey",
        "entity_relation_link", "entity_metadata",
        ["target_entity_metadata_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "entity_relation_link_source_record_id_fkey",
        "entity_relation_link", "entity_data",
        ["source_record_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "entity_relation_link_target_record_id_fkey",
        "entity_relation_link", "entity_data",
        ["target_record_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "entity_relation_link_source_field_id_fkey",
        "entity_relation_link", "field_metadata",
        ["source_field_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "case_entity_link_case_id_fkey",
        "case_entity_link", "cases",
        ["case_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "case_entity_link_entity_metadata_id_fkey",
        "case_entity_link", "entity_metadata",
        ["entity_metadata_id"], ["id"],
        ondelete="CASCADE"
    )
    op.create_foreign_key(
        "case_entity_link_entity_data_id_fkey",
        "case_entity_link", "entity_data",
        ["entity_data_id"], ["id"],
        ondelete="CASCADE"
    )
