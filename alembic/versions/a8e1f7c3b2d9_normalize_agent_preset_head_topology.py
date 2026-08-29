"""Normalize agent preset topology onto resource heads.

Revision ID: a8e1f7c3b2d9
Revises: 44d7e75b6f4c
Create Date: 2026-08-28 19:00:00.000000

This is a roll-forward data migration: it derives the initial head-owned
topology from each preset's current legacy projection, while leaving every
legacy field and version-owned edge intact for application rollback.

The upgrade locks all source and target tables before taking the snapshot,
creates the missing subagent edge table, then validates and backfills both
subagent and skill head edges in the same transaction. Any invalid reference
aborts the migration before the transaction commits.
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
    # Freeze every legacy topology source and existing head-edge target before
    # deriving the canonical snapshot. Old topology writers should still be
    # drained before deployment; this closes the remaining snapshot race.
    op.execute(
        sa.text(
            """
            LOCK TABLE
                agent_preset,
                agent_preset_version,
                agent_preset_version_skill,
                agent_preset_skill,
                skill,
                skill_version
            IN SHARE ROW EXCLUSIVE MODE
            """
        )
    )

    # The SQL asset expects this table to exist and populates it alongside the
    # already-existing agent_preset_skill head-edge table.
    _create_subagent_head_edges()

    # Validate first and fail closed, then materialize the current desired
    # topology. The revision-scoped asset remains part of this migration.
    _execute_sql_asset("backfill.sql")

    # Backfill runs with migration privileges; application access is subject to
    # workspace RLS only after the canonical table contains a complete snapshot.
    op.execute(enable_workspace_table_rls("agent_preset_subagent"))


def downgrade() -> None:
    # No reverse backfill is necessary: upgrade deliberately leaves all legacy
    # version-owned topology untouched for the old application code to consume.
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
