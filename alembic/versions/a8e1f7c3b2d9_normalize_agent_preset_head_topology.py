"""Normalize agent preset topology onto resource heads.

Revision ID: a8e1f7c3b2d9
Revises: 44d7e75b6f4c
Create Date: 2026-08-28 19:00:00.000000

The legacy topology projections remain in place for rollback compatibility.
New application code treats ``agent_preset_subagent`` and
``agent_preset_skill`` as the canonical head-owned topology.
"""

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

revision: str = "a8e1f7c3b2d9"
down_revision: str | None = "44d7e75b6f4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_ASSET_DIR = Path(__file__).resolve().parent.parent / "sql" / revision


def _execute_sql_asset(filename: str) -> None:
    """Execute one immutable SQL asset in Alembic's active transaction."""

    op.get_bind().exec_driver_sql(
        (_SQL_ASSET_DIR / filename).read_text(encoding="utf-8")
    )


def _create_subagent_head_edges() -> None:
    op.create_table(
        "agent_preset_subagent",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("parent_preset_id", sa.UUID(), nullable=False),
        sa.Column("child_preset_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("max_turns", sa.Integer(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), nullable=False),
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
            "max_turns IS NULL OR max_turns >= 1",
            name=op.f("ck_agent_preset_subagent_max_turns_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_preset_id"],
            ["agent_preset.id"],
            name=op.f("fk_agent_preset_subagent_parent_preset_id_agent_preset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["child_preset_id"],
            ["agent_preset.id"],
            name=op.f("fk_agent_preset_subagent_child_preset_id_agent_preset"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_preset_subagent_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id",
            name=op.f("pk_agent_preset_subagent"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "parent_preset_id",
            "alias",
            name="uq_agent_preset_subagent_workspace_parent_alias",
        ),
    )
    op.create_index(
        op.f("ix_agent_preset_subagent_id"),
        "agent_preset_subagent",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_agent_preset_subagent_parent_preset_id"),
        "agent_preset_subagent",
        ["parent_preset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_preset_subagent_child_preset_id"),
        "agent_preset_subagent",
        ["child_preset_id"],
        unique=False,
    )


def upgrade() -> None:
    # Freeze every legacy topology source and shadow target for the transaction.
    # Old topology writers should still be drained before deployment; the lock
    # closes the race between that operational gate and the migration snapshot.
    _execute_sql_asset("lock.sql")
    _create_subagent_head_edges()
    _execute_sql_asset("backfill.sql")
    op.execute(enable_workspace_table_rls("agent_preset_subagent"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("agent_preset_subagent"))
    op.drop_index(
        op.f("ix_agent_preset_subagent_child_preset_id"),
        table_name="agent_preset_subagent",
    )
    op.drop_index(
        op.f("ix_agent_preset_subagent_parent_preset_id"),
        table_name="agent_preset_subagent",
    )
    op.drop_index(
        op.f("ix_agent_preset_subagent_id"),
        table_name="agent_preset_subagent",
    )
    op.drop_table("agent_preset_subagent")
