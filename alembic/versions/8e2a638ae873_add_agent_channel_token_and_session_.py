"""add agent channel token and session channel context

Revision ID: 8e2a638ae873
Revises: 929ee467543f
Create Date: 2026-03-02 15:59:52.906800

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e2a638ae873"
down_revision: str | None = "929ee467543f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_channel_token",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agent_preset_id", sa.UUID(), nullable=False),
        sa.Column("channel_type", sa.String(length=50), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
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
            ["agent_preset_id"],
            ["agent_preset.id"],
            name=op.f("fk_agent_channel_token_agent_preset_id_agent_preset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_channel_token_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_channel_token")),
    )
    op.create_index(
        op.f("ix_agent_channel_token_agent_preset_id"),
        "agent_channel_token",
        ["agent_preset_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_channel_token_agent_preset_id_channel_type_active",
        "agent_channel_token",
        ["agent_preset_id", "channel_type"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
    )
    op.create_index(
        op.f("ix_agent_channel_token_id"), "agent_channel_token", ["id"], unique=True
    )
    op.create_index(
        "ix_agent_channel_token_workspace_id_channel_type",
        "agent_channel_token",
        ["workspace_id", "channel_type"],
        unique=False,
    )
    op.add_column(
        "agent_session",
        sa.Column(
            "channel_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_session", "channel_context")
    op.drop_index(
        "ix_agent_channel_token_workspace_id_channel_type",
        table_name="agent_channel_token",
    )
    op.drop_index(op.f("ix_agent_channel_token_id"), table_name="agent_channel_token")
    op.drop_index(
        "ix_agent_channel_token_agent_preset_id_channel_type_active",
        table_name="agent_channel_token",
        postgresql_where=sa.text("is_active IS TRUE"),
    )
    op.drop_index(
        op.f("ix_agent_channel_token_agent_preset_id"), table_name="agent_channel_token"
    )
    op.drop_table("agent_channel_token")
