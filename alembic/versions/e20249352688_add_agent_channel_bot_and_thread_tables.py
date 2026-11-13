"""Add agent channel bots and thread links.

Revision ID: e20249352688
Revises: ec392d96f2b0
Create Date: 2026-11-10 12:00:00.000000

Creates ``agent_channel_bot`` and ``agent_channel_thread`` with workspace RLS.
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
revision: str = "e20249352688"
down_revision: str | None = "ec392d96f2b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("agent_channel_bot", "agent_channel_thread")


def upgrade() -> None:
    # The foreign keys lock agent_session and agent_preset against writes; fail
    # fast rather than queue behind a long transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.create_table(
        "agent_channel_bot",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("agent_preset_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="active", nullable=False),
        sa.Column("transport", sa.String(), server_default="http", nullable=False),
        sa.Column("provider_tenant_id", sa.String(), nullable=True),
        sa.Column("provider_enterprise_id", sa.String(), nullable=True),
        sa.Column("provider_app_id", sa.String(), nullable=True),
        sa.Column("provider_bot_user_id", sa.String(), nullable=True),
        sa.Column(
            "granted_scopes",
            postgresql.ARRAY(sa.String()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("manifest_version", sa.Integer(), nullable=False),
        sa.Column("encrypted_credentials", sa.LargeBinary(), nullable=False),
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
            "provider IN ('slack', 'teams')", name=op.f("ck_agent_channel_bot_provider")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'disconnected', 'needs_reconnect')",
            name=op.f("ck_agent_channel_bot_status"),
        ),
        sa.CheckConstraint(
            "transport IN ('http', 'socket')",
            name=op.f("ck_agent_channel_bot_transport"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_preset_id"],
            ["agent_preset.id"],
            name=op.f("fk_agent_channel_bot_agent_preset_id_agent_preset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_channel_bot_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_channel_bot")),
        sa.UniqueConstraint(
            "id", "workspace_id", name=op.f("uq_agent_channel_bot_id_workspace_id")
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_tenant_id",
            "provider_app_id",
            name=op.f("uq_agent_channel_bot_provider_tenant_app"),
        ),
    )
    op.create_index(
        op.f("ix_agent_channel_bot_agent_preset_id"),
        "agent_channel_bot",
        ["agent_preset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_channel_bot_workspace_id"),
        "agent_channel_bot",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "agent_channel_thread",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("bot_id", sa.UUID(), nullable=False),
        sa.Column("thread", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("thread_key", sa.String(), nullable=False),
        sa.Column("sync_enabled", sa.Boolean(), server_default="true", nullable=False),
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
            ["bot_id", "workspace_id"],
            ["agent_channel_bot.id", "agent_channel_bot.workspace_id"],
            name=op.f("fk_agent_channel_thread_bot_id_workspace_id_agent_channel_bot"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "workspace_id"],
            ["agent_session.id", "agent_session.workspace_id"],
            name=op.f("fk_agent_channel_thread_session_id_workspace_id_agent_session"),
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_channel_thread_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_agent_channel_thread")),
        sa.UniqueConstraint(
            "bot_id",
            "thread_key",
            name=op.f("uq_agent_channel_thread_bot_id_thread_key"),
        ),
    )
    op.create_index(
        op.f("ix_agent_channel_thread_workspace_id"),
        "agent_channel_thread",
        ["workspace_id"],
        unique=False,
    )
    for table in TABLES:
        op.execute(enable_workspace_table_rls(table))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    for table in reversed(TABLES):
        op.execute(disable_workspace_table_rls(table))
    op.drop_index(
        op.f("ix_agent_channel_thread_workspace_id"), table_name="agent_channel_thread"
    )
    op.drop_table("agent_channel_thread")
    op.drop_index(
        op.f("ix_agent_channel_bot_workspace_id"), table_name="agent_channel_bot"
    )
    op.drop_index(
        op.f("ix_agent_channel_bot_agent_preset_id"), table_name="agent_channel_bot"
    )
    op.drop_table("agent_channel_bot")
