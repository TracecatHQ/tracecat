"""scope case numbers to workspace

Revision ID: 13cfd6e83e36
Revises: 8e2a638ae873
Create Date: 2026-03-06 13:47:47.535750

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "13cfd6e83e36"
down_revision: str | None = "8e2a638ae873"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column(
            "last_case_number",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # `case_number` used to be a global identity column. Drop the identity first so
    # future inserts must use workspace-scoped allocation in application code.
    op.execute('ALTER TABLE "case" ALTER COLUMN case_number DROP IDENTITY IF EXISTS')
    op.drop_index("ix_case_case_number", table_name="case")
    op.execute(
        """
        WITH renumbered AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY workspace_id
                    ORDER BY case_number
                ) AS next_case_number
            FROM "case"
        )
        UPDATE "case" AS c
        SET case_number = renumbered.next_case_number
        FROM renumbered
        WHERE c.id = renumbered.id
        """
    )
    op.execute(
        """
        UPDATE workspace AS w
        SET last_case_number = counters.max_case_number
        FROM (
            SELECT workspace_id, max(case_number) AS max_case_number
            FROM "case"
            GROUP BY workspace_id
        ) AS counters
        WHERE w.id = counters.workspace_id
        """
    )
    op.create_unique_constraint(
        "uq_case_workspace_case_number",
        "case",
        ["workspace_id", "case_number"],
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade is not supported because historical case numbers are renumbered."
    )
