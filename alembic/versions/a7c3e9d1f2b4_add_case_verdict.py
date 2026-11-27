"""Add case verdict.

Revision ID: a7c3e9d1f2b4
Revises: d55f7e6e72a2
Create Date: 2026-11-27 04:00:00.000000

Additive: a ``caseverdict`` enum type, a nullable ``case.verdict`` column
(existing cases keep a null verdict), and a ``VERDICT_CHANGED`` case event
type.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7c3e9d1f2b4"
down_revision: str | None = "d55f7e6e72a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERDICT_VALUES = (
    "UNKNOWN",
    "FALSE_POSITIVE",
    "TRUE_POSITIVE",
    "DISREGARD",
    "SUSPICIOUS",
    "BENIGN",
    "TEST",
)


def upgrade() -> None:
    op.execute("ALTER TYPE caseeventtype ADD VALUE IF NOT EXISTS 'VERDICT_CHANGED'")
    values = ", ".join(f"'{value}'" for value in _VERDICT_VALUES)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'caseverdict') THEN
                CREATE TYPE caseverdict AS ENUM ({values});
            END IF;
        END
        $$
        """
    )
    # Adding a nullable column without a default is a catalog-only change, but
    # it still needs a brief ACCESS EXCLUSIVE lock; fail fast rather than queue
    # every case read behind a long-running transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute('ALTER TABLE "case" ADD COLUMN IF NOT EXISTS verdict caseverdict')


def downgrade() -> None:
    # PostgreSQL cannot drop enum values, so VERDICT_CHANGED stays on
    # caseeventtype. The previous application version raises LookupError when
    # it loads case events recorded with it, so delete them before rolling back.
    op.execute("DELETE FROM case_event WHERE type = 'VERDICT_CHANGED'")
    op.execute('ALTER TABLE "case" DROP COLUMN IF EXISTS verdict')
    op.execute("DROP TYPE IF EXISTS caseverdict")
