"""add agent preset versions

Revision ID: 6171727be56a
Revises: 13cfd6e83e36
Create Date: 2026-03-07 15:37:17.234056

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6171727be56a"
down_revision: str | None = "13cfd6e83e36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_preset_version",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("preset_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("model_provider", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column(
            "output_type", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("namespaces", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "tool_approvals", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "mcp_integrations", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column(
            "enable_internet_access",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
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
            ["preset_id"],
            ["agent_preset.id"],
            name=op.f("fk_agent_preset_version_preset_id_agent_preset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_preset_version_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_preset_version")),
        sa.UniqueConstraint(
            "workspace_id",
            "preset_id",
            "version",
            name=op.f("uq_agent_preset_version_workspace_id_preset_id_version"),
        ),
    )
    op.create_index(
        op.f("ix_agent_preset_version_id"),
        "agent_preset_version",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_agent_preset_version_preset_id"),
        "agent_preset_version",
        ["preset_id"],
        unique=False,
    )
    op.add_column(
        "agent_preset",
        sa.Column("current_version_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "agent_session",
        sa.Column("agent_preset_version_id", sa.UUID(), nullable=True),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO agent_preset_version (
                id,
                preset_id,
                version,
                instructions,
                model_name,
                model_provider,
                base_url,
                output_type,
                actions,
                namespaces,
                tool_approvals,
                mcp_integrations,
                retries,
                enable_internet_access,
                workspace_id,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid(),
                ap.id,
                1,
                ap.instructions,
                ap.model_name,
                ap.model_provider,
                ap.base_url,
                ap.output_type,
                ap.actions,
                ap.namespaces,
                ap.tool_approvals,
                ap.mcp_integrations,
                ap.retries,
                ap.enable_internet_access,
                ap.workspace_id,
                ap.created_at,
                ap.updated_at
            FROM agent_preset AS ap
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE agent_preset AS ap
            SET current_version_id = apv.id
            FROM agent_preset_version AS apv
            WHERE apv.preset_id = ap.id
              AND apv.version = 1
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE agent_session AS session
            SET agent_preset_version_id = ap.current_version_id
            FROM agent_preset AS ap
            WHERE session.agent_preset_version_id IS NULL
              AND ap.current_version_id IS NOT NULL
              AND (
                session.agent_preset_id = ap.id
                OR (
                  session.agent_preset_id IS NULL
                  AND session.entity_type = 'agent_preset'
                  AND session.entity_id = ap.id
                )
              )
            """
        )
    )

    op.create_foreign_key(
        op.f("fk_agent_preset_current_version_id_agent_preset_version"),
        "agent_preset",
        "agent_preset_version",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_agent_session_agent_preset_version_id_agent_preset_version"),
        "agent_session",
        "agent_preset_version",
        ["agent_preset_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_agent_session_agent_preset_version_id_agent_preset_version"),
        "agent_session",
        type_="foreignkey",
    )
    op.drop_column("agent_session", "agent_preset_version_id")
    op.drop_constraint(
        op.f("fk_agent_preset_current_version_id_agent_preset_version"),
        "agent_preset",
        type_="foreignkey",
    )
    op.drop_column("agent_preset", "current_version_id")
    op.drop_index(
        op.f("ix_agent_preset_version_preset_id"),
        table_name="agent_preset_version",
    )
    op.drop_index(op.f("ix_agent_preset_version_id"), table_name="agent_preset_version")
    op.drop_table("agent_preset_version")
