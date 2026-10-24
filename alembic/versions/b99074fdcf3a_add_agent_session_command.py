"""Add agent_session_command

Revision ID: b99074fdcf3a
Revises: c4e8a2f6d9b1
Create Date: 2026-10-23 17:30:00.000000

Additive: a new workspace-scoped table with its RLS policy. No existing table
changes, so the previous app version runs unchanged against the new schema.
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
revision: str = "b99074fdcf3a"
down_revision: str | None = "c4e8a2f6d9b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "agent_session_command"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("root_session_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.UUID(), nullable=True),
        sa.Column("generation", sa.UUID(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("receipt", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.CheckConstraint(
            "type IN ('submit', 'steer', 'decide', 'abort', 'withdraw')",
            name=op.f("ck_agent_session_command_type"),
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'accepted', 'applied', 'rejected', 'withdrawn', 'settled')",
            name=op.f("ck_agent_session_command_state"),
        ),
        sa.ForeignKeyConstraint(
            ["root_session_id"],
            ["agent_session.id"],
            name=op.f("fk_agent_session_command_root_session_id_agent_session"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_session_command_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_session_command")),
        sa.UniqueConstraint(
            "root_session_id",
            "request_id",
            name=op.f("uq_agent_session_command_root_session_id_request_id"),
        ),
        sa.UniqueConstraint(
            "root_session_id",
            "seq",
            name=op.f("uq_agent_session_command_root_session_id_seq"),
        ),
    )
    op.create_index(
        "ix_agent_session_command_active",
        TABLE,
        ["root_session_id", "seq"],
        unique=False,
        postgresql_where=sa.text("state IN ('queued', 'accepted', 'applied')"),
    )
    op.execute(enable_workspace_table_rls(TABLE))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls(TABLE))
    op.drop_index("ix_agent_session_command_active", table_name=TABLE)
    op.drop_table(TABLE)
