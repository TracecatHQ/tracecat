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
    # Cascading deletes from case and case_event look rows up by these keys.
    op.create_index(
        op.f("ix_case_trigger_outbox_case_id"), "case_trigger_outbox", ["case_id"]
    )
    op.create_index(
        op.f("ix_case_trigger_outbox_event_id"), "case_trigger_outbox", ["event_id"]
    )
    op.create_index(
        "ix_case_trigger_outbox_dispatch_order",
        "case_trigger_outbox",
        ["updated_at", "surrogate_id"],
        unique=False,
    )
    op.execute(enable_workspace_table_rls("case_trigger_outbox"))
    # Case writes now check for an online trigger in the same transaction.
    op.create_index(
        "ix_case_trigger_online_workspace_id",
        "case_trigger",
        ["workspace_id"],
        postgresql_where=sa.text("status = 'online'"),
    )


def downgrade() -> None:
    op.drop_index("ix_case_trigger_online_workspace_id", table_name="case_trigger")
    op.execute(disable_workspace_table_rls("case_trigger_outbox"))
    op.drop_index(
        "ix_case_trigger_outbox_dispatch_order", table_name="case_trigger_outbox"
    )
    op.drop_index(
        op.f("ix_case_trigger_outbox_event_id"), table_name="case_trigger_outbox"
    )
    op.drop_index(
        op.f("ix_case_trigger_outbox_case_id"), table_name="case_trigger_outbox"
    )
    op.drop_table("case_trigger_outbox")
