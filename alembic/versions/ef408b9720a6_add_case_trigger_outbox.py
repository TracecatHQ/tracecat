"""add case trigger outbox

Revision ID: ef408b9720a6
Revises: 4a17af960e05
Create Date: 2026-11-03 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "ef408b9720a6"
down_revision: str | None = "4a17af960e05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_trigger_outbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("extras", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["case.id"],
            name=op.f("fk_case_trigger_outbox_case_id_case"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["case_event.id"],
            name=op.f("fk_case_trigger_outbox_event_id_case_event"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_case_trigger_outbox_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_case_trigger_outbox")),
        sa.UniqueConstraint("id", name=op.f("uq_case_trigger_outbox_id")),
    )
    op.execute(enable_workspace_table_rls("case_trigger_outbox"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("case_trigger_outbox"))
    op.drop_table("case_trigger_outbox")
