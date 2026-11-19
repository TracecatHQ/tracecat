"""add agent channel users and bot channel access policy

Revision ID: 94caf98ad60f
Revises: 14588b612098
Create Date: 2026-11-19 12:52:00.000000

Additive only: creates agent_channel_user and adds agent_channel_bot.channel_access_policy
with a server default, so existing bots read linked_approvals and the previous app
version, which never reads either, keeps working.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import disable_org_table_rls, enable_org_table_rls

# revision identifiers, used by Alembic.
revision: str = "94caf98ad60f"
down_revision: str | None = "14588b612098"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_channel_user",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_tenant_id", sa.String(length=255), nullable=False),
        sa.Column("provider_user_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("link_method", sa.String(length=16), nullable=False),
        sa.Column(
            "linked_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
            "link_method IN ('email', 'challenge')",
            name=op.f("ck_agent_channel_user_link_method"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_agent_channel_user_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_agent_channel_user_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_channel_user")),
        sa.UniqueConstraint(
            "organization_id",
            "provider",
            "provider_tenant_id",
            "provider_user_id",
            name="uq_agent_channel_user_org_provider_identity",
        ),
    )
    op.create_index(
        op.f("ix_agent_channel_user_user_id"),
        "agent_channel_user",
        ["user_id"],
        unique=False,
    )
    op.execute(enable_org_table_rls("agent_channel_user"))
    # Fail fast instead of queueing bot reads behind a long transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    # A constant default is metadata-only: no table rewrite.
    op.add_column(
        "agent_channel_bot",
        sa.Column(
            "channel_access_policy",
            sa.String(length=32),
            server_default="linked_approvals",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_agent_channel_bot_channel_access_policy"),
        "agent_channel_bot",
        "channel_access_policy IN ('linked_only', 'linked_approvals', 'open')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_channel_bot_channel_access_policy"),
        "agent_channel_bot",
        type_="check",
    )
    op.drop_column("agent_channel_bot", "channel_access_policy")
    op.execute(disable_org_table_rls("agent_channel_user"))
    op.drop_index(
        op.f("ix_agent_channel_user_user_id"), table_name="agent_channel_user"
    )
    op.drop_table("agent_channel_user")
