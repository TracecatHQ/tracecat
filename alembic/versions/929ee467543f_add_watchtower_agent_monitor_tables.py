"""add watchtower agent monitor tables

Revision ID: 929ee467543f
Revises: 2bedc5514ca9
Create Date: 2026-03-03 19:42:59.649801

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "929ee467543f"
down_revision: str | None = "2bedc5514ca9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watchtower_agent",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("fingerprint_hash", sa.String(length=64), nullable=False),
        sa.Column("agent_type", sa.String(length=32), nullable=False),
        sa.Column("agent_source", sa.String(length=32), nullable=False),
        sa.Column("agent_icon_key", sa.String(length=64), nullable=True),
        sa.Column("raw_user_agent", sa.Text(), nullable=True),
        sa.Column(
            "raw_client_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("auth_client_id", sa.String(length=255), nullable=True),
        sa.Column("last_user_id", sa.UUID(), nullable=True),
        sa.Column("last_user_email", sa.String(length=320), nullable=True),
        sa.Column("last_user_name", sa.String(length=255), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("blocked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("blocked_by_user_id", sa.UUID(), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
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
            ["blocked_by_user_id"],
            ["user.id"],
            name=op.f("fk_watchtower_agent_blocked_by_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_user_id"],
            ["user.id"],
            name=op.f("fk_watchtower_agent_last_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_watchtower_agent_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_watchtower_agent")),
        sa.UniqueConstraint(
            "organization_id",
            "fingerprint_hash",
            name="uq_watchtower_agent_organization_id_fingerprint_hash",
        ),
    )
    op.create_index(
        op.f("ix_watchtower_agent_id"), "watchtower_agent", ["id"], unique=True
    )
    op.create_index(
        "ix_wt_agent_org_blocked",
        "watchtower_agent",
        ["organization_id", "blocked_at"],
        unique=False,
    )
    op.create_index(
        "ix_wt_agent_org_seen",
        "watchtower_agent",
        ["organization_id", "last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_wt_agent_org_type",
        "watchtower_agent",
        ["organization_id", "agent_type"],
        unique=False,
    )

    op.create_table(
        "watchtower_agent_session",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=True),
        sa.Column("session_state", sa.String(length=32), nullable=False),
        sa.Column("auth_transaction_id", sa.String(length=128), nullable=True),
        sa.Column("auth_client_id", sa.String(length=255), nullable=True),
        sa.Column("oauth_callback_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("agent_session_id", sa.String(length=255), nullable=True),
        sa.Column("initialize_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("user_email", sa.String(length=320), nullable=True),
        sa.Column("user_name", sa.String(length=255), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column("revoked_by_user_id", sa.UUID(), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
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
            ["agent_id"],
            ["watchtower_agent.id"],
            name=op.f("fk_watchtower_agent_session_agent_id_watchtower_agent"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_watchtower_agent_session_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["user.id"],
            name=op.f("fk_watchtower_agent_session_revoked_by_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_watchtower_agent_session_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_watchtower_agent_session_workspace_id_workspace"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_watchtower_agent_session")
        ),
    )
    op.create_index(
        op.f("ix_watchtower_agent_session_agent_id"),
        "watchtower_agent_session",
        ["agent_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_watchtower_agent_session_id"),
        "watchtower_agent_session",
        ["id"],
        unique=True,
    )
    op.create_index(
        "ix_wt_agent_sess_org_auth_cb_seen",
        "watchtower_agent_session",
        ["organization_id", "auth_client_id", "oauth_callback_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_wt_agent_sess_org_session_id_uq",
        "watchtower_agent_session",
        ["organization_id", "agent_session_id"],
        unique=True,
        postgresql_where=sa.text("agent_session_id IS NOT NULL"),
    )
    op.create_index(
        "ix_wt_agent_sess_org_state_seen",
        "watchtower_agent_session",
        ["organization_id", "session_state", "last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_wt_agent_sess_org_user_seen",
        "watchtower_agent_session",
        ["organization_id", "user_id", "last_seen_at"],
        unique=False,
    )

    op.create_table(
        "watchtower_agent_tool_call",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("agent_session_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("call_status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "args_redacted",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_redacted", sa.Text(), nullable=True),
        sa.Column(
            "called_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.UUID(), nullable=False),
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
            ["agent_id"],
            ["watchtower_agent.id"],
            name=op.f("fk_watchtower_agent_tool_call_agent_id_watchtower_agent"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_session_id"],
            ["watchtower_agent_session.id"],
            name=op.f(
                "fk_watchtower_agent_tool_call_agent_session_id_watchtower_agent_session"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_watchtower_agent_tool_call_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_watchtower_agent_tool_call_workspace_id_workspace"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_watchtower_agent_tool_call")
        ),
    )
    op.create_index(
        op.f("ix_watchtower_agent_tool_call_agent_id"),
        "watchtower_agent_tool_call",
        ["agent_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_watchtower_agent_tool_call_agent_session_id"),
        "watchtower_agent_tool_call",
        ["agent_session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_watchtower_agent_tool_call_id"),
        "watchtower_agent_tool_call",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_watchtower_agent_tool_call_workspace_id"),
        "watchtower_agent_tool_call",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_wt_call_org_agent_called",
        "watchtower_agent_tool_call",
        ["organization_id", "agent_id", "called_at"],
        unique=False,
    )
    op.create_index(
        "ix_wt_call_org_called",
        "watchtower_agent_tool_call",
        ["organization_id", "called_at"],
        unique=False,
    )
    op.create_index(
        "ix_wt_call_org_session_called",
        "watchtower_agent_tool_call",
        ["organization_id", "agent_session_id", "called_at"],
        unique=False,
    )
    op.create_index(
        "ix_wt_call_org_ws_called",
        "watchtower_agent_tool_call",
        ["organization_id", "workspace_id", "called_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_wt_call_org_ws_called", table_name="watchtower_agent_tool_call")
    op.drop_index(
        "ix_wt_call_org_session_called", table_name="watchtower_agent_tool_call"
    )
    op.drop_index("ix_wt_call_org_called", table_name="watchtower_agent_tool_call")
    op.drop_index(
        "ix_wt_call_org_agent_called", table_name="watchtower_agent_tool_call"
    )
    op.drop_index(
        op.f("ix_watchtower_agent_tool_call_workspace_id"),
        table_name="watchtower_agent_tool_call",
    )
    op.drop_index(
        op.f("ix_watchtower_agent_tool_call_id"),
        table_name="watchtower_agent_tool_call",
    )
    op.drop_index(
        op.f("ix_watchtower_agent_tool_call_agent_session_id"),
        table_name="watchtower_agent_tool_call",
    )
    op.drop_index(
        op.f("ix_watchtower_agent_tool_call_agent_id"),
        table_name="watchtower_agent_tool_call",
    )
    op.drop_table("watchtower_agent_tool_call")

    op.drop_index(
        "ix_wt_agent_sess_org_user_seen", table_name="watchtower_agent_session"
    )
    op.drop_index(
        "ix_wt_agent_sess_org_state_seen", table_name="watchtower_agent_session"
    )
    op.drop_index(
        "ix_wt_agent_sess_org_session_id_uq",
        table_name="watchtower_agent_session",
        postgresql_where=sa.text("agent_session_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_wt_agent_sess_org_auth_cb_seen", table_name="watchtower_agent_session"
    )
    op.drop_index(
        op.f("ix_watchtower_agent_session_id"), table_name="watchtower_agent_session"
    )
    op.drop_index(
        op.f("ix_watchtower_agent_session_agent_id"),
        table_name="watchtower_agent_session",
    )
    op.drop_table("watchtower_agent_session")

    op.drop_index("ix_wt_agent_org_type", table_name="watchtower_agent")
    op.drop_index("ix_wt_agent_org_seen", table_name="watchtower_agent")
    op.drop_index("ix_wt_agent_org_blocked", table_name="watchtower_agent")
    op.drop_index(op.f("ix_watchtower_agent_id"), table_name="watchtower_agent")
    op.drop_table("watchtower_agent")
