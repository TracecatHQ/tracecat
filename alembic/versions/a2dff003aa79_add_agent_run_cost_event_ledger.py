"""add agent_run_cost event ledger

Revision ID: a2dff003aa79
Revises: 548aa7691799
Create Date: 2026-04-23 11:23:50.439524

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_org_optional_workspace_table_rls,
    enable_org_optional_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "a2dff003aa79"
down_revision: str | None = "548aa7691799"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_run_cost",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=14, scale=5), nullable=False),
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
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_agent_run_cost_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_run_cost_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_run_cost")),
    )
    op.create_index(op.f("ix_agent_run_cost_id"), "agent_run_cost", ["id"], unique=True)
    op.create_index(
        "ix_agent_run_cost_org_created_at",
        "agent_run_cost",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_agent_run_cost_org_ws_created_at",
        "agent_run_cost",
        ["organization_id", "workspace_id", "created_at"],
        unique=False,
    )
    op.execute(enable_org_optional_workspace_table_rls("agent_run_cost"))


def downgrade() -> None:
    op.execute(disable_org_optional_workspace_table_rls("agent_run_cost"))
    op.drop_index("ix_agent_run_cost_org_ws_created_at", table_name="agent_run_cost")
    op.drop_index("ix_agent_run_cost_org_created_at", table_name="agent_run_cost")
    op.drop_index(op.f("ix_agent_run_cost_id"), table_name="agent_run_cost")
    op.drop_table("agent_run_cost")
