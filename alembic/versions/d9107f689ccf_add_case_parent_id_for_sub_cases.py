"""Add case parent_id for sub-cases.

Revision ID: d9107f689ccf
Revises: d45011587673
Create Date: 2026-10-07 20:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d9107f689ccf"
down_revision: str | None = "d45011587673"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAME = "fk_case_parent_id_case"
_CHECK_NAME = "ck_case_parent_not_self"
_INDEX_NAME = "ix_case_parent_id"


def _add_constraint_if_missing(name: str, definition: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = '{name}' AND conrelid = '"case"'::regclass
            ) THEN
                ALTER TABLE "case" ADD CONSTRAINT "{name}" {definition} NOT VALID;
            END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    for value in ("PARENT_CHANGED", "SUB_CASES_ADDED", "SUB_CASES_REMOVED"):
        op.execute(f"ALTER TYPE caseeventtype ADD VALUE IF NOT EXISTS '{value}'")

    # The concurrent index build below runs outside the migration transaction,
    # so a failed build leaves these changes committed without stamping the
    # revision. Every step is idempotent so a retry can complete the upgrade.
    # Adding a nullable column without a default is a catalog-only change.
    op.execute('ALTER TABLE "case" ADD COLUMN IF NOT EXISTS parent_id UUID')
    # NOT VALID skips the full-table scan under the ACCESS EXCLUSIVE lock; the
    # separate VALIDATE only takes SHARE UPDATE EXCLUSIVE and allows writes.
    _add_constraint_if_missing(
        _FK_NAME,
        'FOREIGN KEY (parent_id) REFERENCES "case" (id) ON DELETE SET NULL',
    )
    _add_constraint_if_missing(
        _CHECK_NAME, "CHECK (parent_id IS NULL OR parent_id <> id)"
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
    # Raw SQL keeps the exact names; `op.drop_constraint` would apply the
    # metadata naming convention and prefix the check constraint name again.
    op.execute(f'ALTER TABLE "case" DROP CONSTRAINT IF EXISTS "{_CHECK_NAME}"')
    op.execute(f'ALTER TABLE "case" DROP CONSTRAINT IF EXISTS "{_FK_NAME}"')
    op.execute('ALTER TABLE "case" DROP COLUMN IF EXISTS parent_id')
