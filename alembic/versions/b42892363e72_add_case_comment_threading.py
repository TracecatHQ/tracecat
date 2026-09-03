"""add case comment threading

Revision ID: b42892363e72
Revises: 13cfd6e83e36
Create Date: 2026-03-08 00:10:08.713211

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b42892363e72"
down_revision: str | None = "13cfd6e83e36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "case_comment",
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.execute(
        sa.text(
            """
            UPDATE case_comment AS child
            SET parent_id = NULL
            WHERE parent_id IS NOT NULL
              AND (
                parent_id = id
                OR NOT EXISTS (
                  SELECT 1
                  FROM case_comment AS parent
                  WHERE parent.id = child.parent_id
                )
                OR EXISTS (
                  SELECT 1
                  FROM case_comment AS parent
                  WHERE parent.id = child.parent_id
                    AND parent.case_id <> child.case_id
                )
                OR EXISTS (
                  SELECT 1
                  FROM case_comment AS parent
                  WHERE parent.id = child.parent_id
                    AND parent.parent_id IS NOT NULL
                )
              )
            """
        )
    )

    op.create_unique_constraint(
        op.f("uq_case_comment_case_id_id"),
        "case_comment",
        ["case_id", "id"],
    )
    op.create_check_constraint(
        op.f("ck_case_comment_case_comment_parent_not_self"),
        "case_comment",
        "parent_id IS NULL OR parent_id <> id",
    )
    op.create_foreign_key(
        op.f("fk_case_comment_case_id_parent_id_case_comment"),
        "case_comment",
        "case_comment",
        ["case_id", "parent_id"],
        ["case_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_case_comment_case_id_created_at_surrogate_id",
        "case_comment",
        ["case_id", "created_at", "surrogate_id"],
        unique=False,
    )
    op.create_index(
        "ix_case_comment_case_id_parent_id_created_at_surrogate_id",
        "case_comment",
        ["case_id", "parent_id", "created_at", "surrogate_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_case_comment_case_id_parent_id_created_at_surrogate_id",
        table_name="case_comment",
    )
    op.drop_index(
        "ix_case_comment_case_id_created_at_surrogate_id",
        table_name="case_comment",
    )
    op.drop_constraint(
        op.f("fk_case_comment_case_id_parent_id_case_comment"),
        "case_comment",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_case_comment_case_comment_parent_not_self"),
        "case_comment",
        type_="check",
    )
    op.drop_constraint(
        op.f("uq_case_comment_case_id_id"),
        "case_comment",
        type_="unique",
    )
    op.drop_column("case_comment", "deleted_at")
