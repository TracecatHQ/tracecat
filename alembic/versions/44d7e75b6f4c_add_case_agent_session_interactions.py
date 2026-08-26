"""Add case-agent session interactions.

Revision ID: 44d7e75b6f4c
Revises: 598b32358ec5
Create Date: 2026-08-26 17:17:55.915972

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "44d7e75b6f4c"
down_revision: str | None = "598b32358ec5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_agent_session_interaction",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("agent_session_id", sa.UUID(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
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
            ["agent_session_id"],
            ["agent_session.id"],
            name=op.f(
                "fk_case_agent_session_interaction_agent_session_id_agent_session"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["case.id"],
            name=op.f("fk_case_agent_session_interaction_case_id_case"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_case_agent_session_interaction_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id",
            name=op.f("pk_case_agent_session_interaction"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "case_id",
            "agent_session_id",
            "operation",
            name="uq_case_agent_session_interaction_ws_case_session_operation",
        ),
    )
    op.create_index(
        op.f("ix_case_agent_session_interaction_agent_session_id"),
        "case_agent_session_interaction",
        ["agent_session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_agent_session_interaction_case_id"),
        "case_agent_session_interaction",
        ["case_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_agent_session_interaction_id"),
        "case_agent_session_interaction",
        ["id"],
        unique=True,
    )
    op.execute(enable_workspace_table_rls("case_agent_session_interaction"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("case_agent_session_interaction"))
    op.drop_table("case_agent_session_interaction")
