"""Add platform-owned channel installs and channel routes

Revision ID: 11e685c62cfd
Revises: 183d302a8d08
Create Date: 2026-11-14 23:46:58.000000

Adds ``agent_channel_bot.app_owner`` (existing rows stay ``workspace``) and
``agent_channel_route`` with workspace RLS. Additive, but the previous app reads platform
installs as custom-app bots: disconnect them before rolling the app back. Downgrading
refuses while any platform install row remains.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "11e685c62cfd"
down_revision: str | None = "183d302a8d08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The keys lock agent_channel_bot and agent_preset; fail fast rather than
    # queue behind a long transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "agent_channel_bot",
        sa.Column("app_owner", sa.String(), server_default="workspace", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_agent_channel_bot_app_owner"),
        "agent_channel_bot",
        "app_owner IN ('workspace', 'platform')",
    )
    op.create_table(
        "agent_channel_route",
        sa.Column("bot_id", sa.UUID(), nullable=False),
        sa.Column("provider_channel_id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("agent_preset_id", sa.UUID(), nullable=False),
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
            ["agent_preset_id", "workspace_id"],
            ["agent_preset.id", "agent_preset.workspace_id"],
            name=op.f(
                "fk_agent_channel_route_agent_preset_id_workspace_id_agent_preset"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bot_id", "workspace_id"],
            ["agent_channel_bot.id", "agent_channel_bot.workspace_id"],
            name=op.f("fk_agent_channel_route_bot_id_workspace_id_agent_channel_bot"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_channel_route_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "bot_id", "provider_channel_id", name=op.f("pk_agent_channel_route")
        ),
    )
    op.create_index(
        op.f("ix_agent_channel_route_agent_preset_id"),
        "agent_channel_route",
        ["agent_preset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_channel_route_workspace_id"),
        "agent_channel_route",
        ["workspace_id"],
        unique=False,
    )
    op.execute(enable_workspace_table_rls("agent_channel_route"))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    # The previous schema has no owner column, so these rows would turn into custom-app bots.
    platform_installs = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM agent_channel_bot WHERE app_owner = 'platform'"
            )
        )
        .scalar_one()
    )
    if platform_installs:
        raise RuntimeError(
            f"Downgrade refused: {platform_installs} Tracecat for Slack install row(s)"
            " remain. Disconnect each install from its workspace's Channels page, then"
            " delete the agent_channel_bot rows with app_owner = 'platform' and rerun"
            " the downgrade."
        )
    op.execute(disable_workspace_table_rls("agent_channel_route"))
    op.drop_index(
        op.f("ix_agent_channel_route_workspace_id"), table_name="agent_channel_route"
    )
    op.drop_index(
        op.f("ix_agent_channel_route_agent_preset_id"),
        table_name="agent_channel_route",
    )
    op.drop_table("agent_channel_route")
    op.drop_constraint(
        op.f("ck_agent_channel_bot_app_owner"), "agent_channel_bot", type_="check"
    )
    op.drop_column("agent_channel_bot", "app_owner")
