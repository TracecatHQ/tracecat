"""Add agent turn provenance snapshots.

Revision ID: b4e6c8a2d0f1
Revises: 44320bf05445
Create Date: 2026-07-08 00:00:00.000000

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
revision: str = "b4e6c8a2d0f1"
down_revision: str | None = "44320bf05445"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_turn_provenance",
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("wf_exec_id", sa.String(), nullable=False),
        sa.Column(
            "resolved_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_turn_provenance_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_turn_provenance")),
    )
    op.create_index(
        op.f("ix_agent_turn_provenance_workspace_id"),
        "agent_turn_provenance",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_turn_provenance_session_id"),
        "agent_turn_provenance",
        ["session_id"],
        unique=False,
    )
    op.execute(enable_workspace_table_rls("agent_turn_provenance"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("agent_turn_provenance"))
    op.drop_index(
        op.f("ix_agent_turn_provenance_session_id"),
        table_name="agent_turn_provenance",
    )
    op.drop_index(
        op.f("ix_agent_turn_provenance_workspace_id"),
        table_name="agent_turn_provenance",
    )
    op.drop_table("agent_turn_provenance")
