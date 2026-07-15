"""Contract legacy agent preset and skill representations.

Revision ID: c7d9e1f3a5b2
Revises: d2e4f6a8b0c1
Create Date: 2026-07-15 00:00:00.000000

The cutover application reads and writes only immutable preset versions and
ResourceHead edges. This revision removes the rollout bridge and legacy copies
without changing the authoritative representation.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import disable_workspace_table_rls

revision: str = "c7d9e1f3a5b2"
down_revision: str | None = "d2e4f6a8b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUBAGENTS_NOT_NULL_CONSTRAINT = "ck_agent_preset_version_subagents_enabled_not_null"
SUBAGENTS_SYNC_FUNCTION = "sync_agent_preset_version_subagents_enabled"
SUBAGENTS_SYNC_TRIGGER = "trg_agent_preset_version_subagents_enabled"


def _drop_expand_writer_bridge() -> None:
    """Remove compatibility DDL before dropping its legacy source column."""
    op.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS {SUBAGENTS_SYNC_TRIGGER} ON agent_preset_version"
        )
    )
    op.execute(sa.text(f"DROP FUNCTION IF EXISTS {SUBAGENTS_SYNC_FUNCTION}()"))


def _drop_legacy_preset_head() -> None:
    """Keep only mutable ResourceHead metadata on agent_preset."""
    op.execute(disable_workspace_table_rls("agent_preset_skill"))
    op.drop_table("agent_preset_skill")

    op.drop_constraint(
        op.f("fk_agent_preset_catalog_id_agent_catalog"),
        "agent_preset",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_agent_preset_catalog_id"), table_name="agent_preset")
    for column in (
        "instructions",
        "model_name",
        "model_provider",
        "catalog_id",
        "base_url",
        "output_type",
        "actions",
        "namespaces",
        "tool_approvals",
        "mcp_integrations",
        "agents",
        "retries",
        "enable_thinking",
        "enable_internet_access",
    ):
        op.drop_column("agent_preset", column)


def _drop_legacy_version_fields() -> None:
    """Remove version fields superseded by ResourceHead edges."""
    op.drop_index(
        op.f("ix_agent_preset_version_skill_skill_version_id"),
        table_name="agent_preset_version_skill",
    )
    op.drop_constraint(
        op.f("fk_agent_preset_version_skill_skill_version_id_skill_version"),
        "agent_preset_version_skill",
        type_="foreignkey",
    )
    op.drop_column("agent_preset_version_skill", "skill_version_id")
    op.drop_column("agent_preset_version", "agents")


def upgrade() -> None:
    _drop_expand_writer_bridge()

    op.execute(sa.text("SET LOCAL lock_timeout = '1s'"))
    op.alter_column(
        "agent_preset_version",
        "subagents_enabled",
        existing_type=sa.Boolean(),
        nullable=False,
    )
    op.drop_constraint(
        op.f(SUBAGENTS_NOT_NULL_CONSTRAINT),
        "agent_preset_version",
        type_="check",
    )

    _drop_legacy_preset_head()
    _drop_legacy_version_fields()
    op.drop_column("skill", "archived_at")


def downgrade() -> None:
    raise NotImplementedError(
        "Contracted preset snapshots cannot be reconstructed without inventing "
        "historical data; roll back the application to the cutover release instead."
    )
