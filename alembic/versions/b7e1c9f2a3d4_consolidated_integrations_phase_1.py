"""consolidated integrations catalog

Revision ID: b7e1c9f2a3d4
Revises: a3d7c9e8b4f2
Create Date: 2026-05-25 21:00:00.000000

Adds the consolidated Integrations catalog model: an integration catalog table.

Additive only -- no existing tables are dropped. Secret, OAuthIntegration,
WorkspaceOAuthProvider, and MCPIntegration storage continue to drive credential
and MCP behavior.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e1c9f2a3d4"
down_revision: str | None = "a3d7c9e8b4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INTEGRATION_SOURCE_VALUES = ("platform", "workspace")


def upgrade() -> None:
    # --- enums ---------------------------------------------------------
    sa.Enum(*INTEGRATION_SOURCE_VALUES, name="integrationsource").create(op.get_bind())

    # --- integration ---------------------------------------------------
    op.create_table(
        "integration",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("icon_url", sa.String(), nullable=True),
        sa.Column(
            "source",
            postgresql.ENUM(
                *INTEGRATION_SOURCE_VALUES,
                name="integrationsource",
                create_type=False,
            ),
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
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_integration_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration")),
        sa.UniqueConstraint(
            "workspace_id",
            "namespace",
            name="uq_integration_workspace_namespace",
        ),
    )
    op.create_index(op.f("ix_integration_id"), "integration", ["id"], unique=True)
    op.create_index("ix_integration_namespace", "integration", ["namespace"])


def downgrade() -> None:
    op.drop_index("ix_integration_namespace", table_name="integration")
    op.drop_index(op.f("ix_integration_id"), table_name="integration")
    op.drop_table("integration")

    sa.Enum(name="integrationsource").drop(op.get_bind())
