"""Add case parent_id for sub-cases.

Revision ID: d9107f689ccf
Revises: d45011587673
Create Date: 2026-10-07 20:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9107f689ccf"
down_revision: str | None = "d45011587673"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAME = "fk_case_parent_id_case"
_CHECK_NAME = "ck_case_parent_not_self"
_INDEX_NAME = "ix_case_parent_id"


def upgrade() -> None:
    for value in ("PARENT_CHANGED", "SUB_CASES_ADDED", "SUB_CASES_REMOVED"):
        op.execute(f"ALTER TYPE caseeventtype ADD VALUE IF NOT EXISTS '{value}'")

    # Adding a nullable column without a default is a catalog-only change.
    op.add_column("case", sa.Column("parent_id", sa.UUID(), nullable=True))
    # NOT VALID skips the full-table scan under the ACCESS EXCLUSIVE lock; the
    # separate VALIDATE only takes SHARE UPDATE EXCLUSIVE and allows writes.
    op.execute(
        f"""
        ALTER TABLE "case"
        ADD CONSTRAINT "{_FK_NAME}"
        FOREIGN KEY (parent_id) REFERENCES "case" (id) ON DELETE SET NULL
        NOT VALID
        """
    )
    op.execute(
        f"""
        ALTER TABLE "case"
        ADD CONSTRAINT "{_CHECK_NAME}"
        CHECK (parent_id IS NULL OR parent_id <> id)
        NOT VALID
        """
    )
    op.execute(f'ALTER TABLE "case" VALIDATE CONSTRAINT "{_FK_NAME}"')
    op.execute(f'ALTER TABLE "case" VALIDATE CONSTRAINT "{_CHECK_NAME}"')

    with op.get_context().autocommit_block():
        # A failed concurrent build leaves an invalid index behind; rebuild it
        # so a retry never adopts it.
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{_INDEX_NAME}"')
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY "{_INDEX_NAME}"
            ON "case" (parent_id, created_at, id)
            WHERE parent_id IS NOT NULL
            """
        )


def downgrade() -> None:
    # PostgreSQL cannot drop enum values; the added caseeventtype values are
    # left in place and are harmless to the previous application version.
    with op.get_context().autocommit_block():
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{_INDEX_NAME}"')
    op.drop_constraint(_CHECK_NAME, "case", type_="check")
    op.drop_constraint(_FK_NAME, "case", type_="foreignkey")
    op.drop_column("case", "parent_id")
