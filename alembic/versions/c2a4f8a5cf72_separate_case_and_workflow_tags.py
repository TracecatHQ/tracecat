"""Separate case and workflow tags

Revision ID: c2a4f8a5cf72
Revises: 4d35c8153d7b
Create Date: 2025-10-01 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2a4f8a5cf72"
down_revision: str | None = "4d35c8153d7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop existing foreign key from casetag to tag before renaming
    op.drop_constraint("casetag_tag_id_fkey", "casetag", type_="foreignkey")

    # Rename junction table to match new SQLModel definition
    op.rename_table("casetag", "case_tag_link")

    # Create dedicated case_tag table for case-specific tags
    op.create_table(
        "case_tag",
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("(now() AT TIME ZONE 'utc'::text)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("(now() AT TIME ZONE 'utc'::text)"),
            nullable=False,
        ),
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("ref", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("color", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("surrogate_id"),
        sa.UniqueConstraint("name", "owner_id", name="uq_case_tag_name_owner"),
        sa.UniqueConstraint("ref", "owner_id", name="uq_case_tag_ref_owner"),
    )
    op.create_index(op.f("ix_case_tag_id"), "case_tag", ["id"], unique=True)
    op.create_index(op.f("ix_case_tag_name"), "case_tag", ["name"], unique=False)
    op.create_index(op.f("ix_case_tag_ref"), "case_tag", ["ref"], unique=False)

    # Copy existing case tag data from tag table into the new case_tag table
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO case_tag (id, owner_id, name, ref, color, created_at, updated_at)
            SELECT DISTINCT t.id, t.owner_id, t.name, t.ref, t.color, t.created_at, t.updated_at
            FROM tag AS t
            JOIN case_tag_link AS ctl ON ctl.tag_id = t.id
            ON CONFLICT (id) DO NOTHING
            """
        )
    )

    # Recreate foreign key from junction table to new case_tag table
    op.create_foreign_key(
        "case_tag_link_tag_id_fkey",
        "case_tag_link",
        "case_tag",
        ["tag_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Remove foreign key to the case_tag table
    op.drop_constraint("case_tag_link_tag_id_fkey", "case_tag_link", type_="foreignkey")

    # Drop dedicated case_tag table (data remains in tag table)
    op.drop_index(op.f("ix_case_tag_ref"), table_name="case_tag")
    op.drop_index(op.f("ix_case_tag_name"), table_name="case_tag")
    op.drop_index(op.f("ix_case_tag_id"), table_name="case_tag")
    op.drop_table("case_tag")

    # Rename junction table back to original name and restore FK to tag table
    op.rename_table("case_tag_link", "casetag")
    op.create_foreign_key(
        "casetag_tag_id_fkey",
        "casetag",
        "tag",
        ["tag_id"],
        ["id"],
        ondelete="CASCADE",
    )
