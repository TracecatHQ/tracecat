"""group pending approvals persisted one row at a time into batches

Revision ID: 03d80dfe8c1d
Revises: 4ea30f9ea491
Create Date: 2026-11-10 22:00:00.000000

Approval submission groups a pause's rows by ``created_at``, which one
transaction now shares. Workers before this release committed each row of a
pause separately, so a pause still waiting across the upgrade would split into
one-row batches. This revision gives each such pause's pending rows its
earliest ``created_at``. Rows of one pause were written in one loop, while
separate pauses are a model turn and a human decision apart, so a gap over five
seconds starts a new pause.

Downgrade is a no-op: earlier releases do not read batches from ``created_at``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "03d80dfe8c1d"
down_revision: str | None = "4ea30f9ea491"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        WITH ordered AS (
            SELECT
                id,
                workspace_id,
                session_id,
                created_at,
                created_at - lag(created_at) OVER (
                    PARTITION BY workspace_id, session_id ORDER BY created_at
                ) AS gap
            FROM approval
            WHERE status = 'PENDING' AND session_id IS NOT NULL
        ),
        paused AS (
            SELECT
                id,
                workspace_id,
                session_id,
                created_at,
                count(*) FILTER (
                    WHERE gap IS NULL OR gap > interval '5 seconds'
                ) OVER (
                    PARTITION BY workspace_id, session_id ORDER BY created_at
                ) AS pause
            FROM ordered
        ),
        batched AS (
            SELECT
                id,
                min(created_at) OVER (
                    PARTITION BY workspace_id, session_id, pause
                ) AS batch_at
            FROM paused
        )
        UPDATE approval
        SET created_at = batched.batch_at
        FROM batched
        WHERE approval.id = batched.id AND approval.created_at <> batched.batch_at
        """)
    )


def downgrade() -> None:
    pass
