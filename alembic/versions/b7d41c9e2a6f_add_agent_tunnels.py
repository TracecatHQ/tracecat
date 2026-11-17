"""Add tunnels for agent MCP integrations and tool calls.

Revision ID: b7d41c9e2a6f
Revises: e2018a1b2c3d
Create Date: 2026-10-22 01:00:00.000000

Adds nullable columns only; existing rows keep direct egress.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b7d41c9e2a6f"
down_revision: str | None = "e2018a1b2c3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_integration",
        sa.Column("tunnel_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        op.f("ix_mcp_integration_tunnel_id"),
        "mcp_integration",
        ["tunnel_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_mcp_integration_tunnel_id_workspace_tunnel"),
        "mcp_integration",
        "workspace_tunnel",
        ["tunnel_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for table in ("agent_preset", "agent_preset_version"):
        op.add_column(
            table,
            sa.Column(
                "tool_tunnels",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )


def downgrade() -> None:
    for table in ("agent_preset_version", "agent_preset"):
        op.drop_column(table, "tool_tunnels")
    op.drop_constraint(
        op.f("fk_mcp_integration_tunnel_id_workspace_tunnel"),
        "mcp_integration",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_mcp_integration_tunnel_id"), table_name="mcp_integration")
    op.drop_column("mcp_integration", "tunnel_id")
