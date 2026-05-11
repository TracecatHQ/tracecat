"""add case event anchor lookup index

Revision ID: aa5951a3373f
Revises: 0c6bb8f8e1d1
Create Date: 2026-04-23 16:12:47.182400

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aa5951a3373f"
down_revision: str | None = "0c6bb8f8e1d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_case_event_anchor_lookup",
        "case_event",
        ["workspace_id", "case_id", "type", "created_at", "surrogate_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_case_event_anchor_lookup", table_name="case_event")
