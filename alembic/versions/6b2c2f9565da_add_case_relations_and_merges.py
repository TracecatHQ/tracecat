"""Add case relations and merges tables

Revision ID: 6b2c2f9565da
Revises: c2a4f8a5cf72, f04f005837c9
Create Date: 2025-10-15 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6b2c2f9565da"
down_revision: tuple[str, ...] | str | None = ("c2a4f8a5cf72", "f04f005837c9")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_relations",
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("related_case_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["related_case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("surrogate_id"),
        sa.CheckConstraint("case_id <> related_case_id", name="ck_case_relations_no_self"),
        sa.CheckConstraint("case_id < related_case_id", name="ck_case_relations_ordered"),
        sa.UniqueConstraint("case_id", "related_case_id", name="uq_case_relations_pair"),
    )
    op.create_index(
        "ix_case_relations_case_id",
        "case_relations",
        ["case_id"],
    )
    op.create_index(
        "ix_case_relations_related_case_id",
        "case_relations",
        ["related_case_id"],
    )

    op.create_table(
        "case_merges",
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("primary_case_id", sa.UUID(), nullable=False),
        sa.Column("merged_case_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["primary_case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["merged_case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("surrogate_id"),
        sa.CheckConstraint("primary_case_id <> merged_case_id", name="ck_case_merges_no_self"),
        sa.UniqueConstraint("merged_case_id", name="uq_case_merges_secondary"),
        sa.UniqueConstraint(
            "primary_case_id", "merged_case_id", name="uq_case_merges_pair"
        ),
    )
    op.create_index(
        "ix_case_merges_primary_case_id",
        "case_merges",
        ["primary_case_id"],
    )
    op.create_index(
        "ix_case_merges_merged_case_id",
        "case_merges",
        ["merged_case_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_case_merges_merged_case_id", table_name="case_merges")
    op.drop_index("ix_case_merges_primary_case_id", table_name="case_merges")
    op.drop_table("case_merges")
    op.drop_index("ix_case_relations_related_case_id", table_name="case_relations")
    op.drop_index("ix_case_relations_case_id", table_name="case_relations")
    op.drop_table("case_relations")
