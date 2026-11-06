"""order case trigger outbox dispatch by workspace

Revision ID: d986afa25989
Revises: ef408b9720a6
Create Date: 2026-11-06 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d986afa25989"
down_revision: str | None = "ef408b9720a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The dispatcher now takes each workspace's oldest rows in turn. The
    # previous app version still works without the global index: the outbox
    # is a short queue, so sorting it is cheap.
    op.create_index(
        "ix_case_trigger_outbox_workspace_dispatch_order",
        "case_trigger_outbox",
        ["workspace_id", "updated_at", "surrogate_id"],
        unique=False,
    )
    op.drop_index(
        "ix_case_trigger_outbox_dispatch_order", table_name="case_trigger_outbox"
    )


def downgrade() -> None:
    op.create_index(
        "ix_case_trigger_outbox_dispatch_order",
        "case_trigger_outbox",
        ["updated_at", "surrogate_id"],
        unique=False,
    )
    op.drop_index(
        "ix_case_trigger_outbox_workspace_dispatch_order",
        table_name="case_trigger_outbox",
    )
