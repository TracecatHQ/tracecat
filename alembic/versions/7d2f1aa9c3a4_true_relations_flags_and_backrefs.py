"""true relations: add flags + backrefs

Revision ID: 7d2f1aa9c3a4
Revises: 1c2f3a4b5d67
Create Date: 2025-08-23 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d2f1aa9c3a4"
down_revision: str | None = "1c2f3a4b5d67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add backref_field_id to field_metadata (nullable, FK to itself)
    op.add_column(
        "field_metadata",
        sa.Column("backref_field_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_field_metadata_backref_field_id",
        source_table="field_metadata",
        referent_table="field_metadata",
        local_cols=["backref_field_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )

    # Add cardinality flags to record_relation_link
    op.add_column(
        "record_relation_link",
        sa.Column(
            "source_limit_one",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "record_relation_link",
        sa.Column(
            "target_limit_one",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Backfill flags based on field type
    op.execute(
        sa.text(
            """
            UPDATE record_relation_link r
            SET source_limit_one = true
            FROM field_metadata f
            WHERE r.source_field_id = f.id
              AND f.field_type IN ('RELATION_ONE_TO_ONE', 'RELATION_MANY_TO_ONE');
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE record_relation_link r
            SET target_limit_one = true
            FROM field_metadata f
            WHERE r.source_field_id = f.id
              AND f.field_type IN ('RELATION_ONE_TO_ONE');
            """
        )
    )

    # Create partial unique indexes to enforce cardinality
    op.create_index(
        "uq_record_relation_source_single",
        "record_relation_link",
        ["source_record_id", "source_field_id"],
        unique=True,
        postgresql_where=sa.text("source_limit_one = true"),
    )
    op.create_index(
        "uq_record_relation_target_single",
        "record_relation_link",
        ["target_record_id", "source_field_id"],
        unique=True,
        postgresql_where=sa.text("target_limit_one = true"),
    )

    # Drop server defaults to avoid coupling
    op.alter_column(
        "record_relation_link",
        "source_limit_one",
        server_default=None,
        existing_type=sa.Boolean(),
    )
    op.alter_column(
        "record_relation_link",
        "target_limit_one",
        server_default=None,
        existing_type=sa.Boolean(),
    )


def downgrade() -> None:
    # Drop partial unique indexes
    op.drop_index("uq_record_relation_target_single", table_name="record_relation_link")
    op.drop_index("uq_record_relation_source_single", table_name="record_relation_link")

    # Drop cardinality flags
    op.drop_column("record_relation_link", "target_limit_one")
    op.drop_column("record_relation_link", "source_limit_one")

    # Drop backref linkage
    op.drop_constraint(
        "fk_field_metadata_backref_field_id",
        table_name="field_metadata",
        type_="foreignkey",
    )
    op.drop_column("field_metadata", "backref_field_id")
