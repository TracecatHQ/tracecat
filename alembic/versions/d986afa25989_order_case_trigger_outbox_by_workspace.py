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
    # The dispatcher now takes each workspace's oldest rows in turn. The global
    # ix_case_trigger_outbox_dispatch_order index stays for the previous app
    # version during rollout and is dropped in a later release.
    op.create_index(
        "ix_case_trigger_outbox_workspace_dispatch_order",
        "case_trigger_outbox",
        ["workspace_id", "updated_at", "surrogate_id"],
        unique=False,
        # Lets the retry-delay check read the index alone.
        postgresql_include=["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_case_trigger_outbox_workspace_dispatch_order",
        table_name="case_trigger_outbox",
    )
