"""Contract legacy agent preset and skill representations.

Revision ID: c7d9e1f3a5b2
Revises: d2e4f6a8b0c1
Create Date: 2026-07-12 00:00:00.000000

The preceding cutover application reads and writes only immutable preset
versions and ResourceHead edges. This contract release removes the legacy
copies without changing application behavior.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op
from tracecat.db.tenant_rls import disable_workspace_table_rls

revision: str = "c7d9e1f3a5b2"
down_revision: str | None = "d2e4f6a8b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SKILL_SLUG_NOT_NULL_CONSTRAINT = "ck_skill_slug_not_null"
SUBAGENTS_NOT_NULL_CONSTRAINT = "ck_agent_preset_version_subagents_enabled_not_null"
SKILL_ACTIVE_INDEX = "uq_skill_workspace_slug_active"
SKILL_ACTIVE_INDEX_CONTRACT = "uq_skill_workspace_slug_active_contract"


def _index_state(bind: Connection, name: str) -> tuple[bool, bool, str | None]:
    row = bind.execute(
        sa.text(
            """
            SELECT idx.indisvalid,
                   pg_get_expr(idx.indpred, idx.indrelid) AS predicate
            FROM pg_index AS idx
            WHERE idx.indexrelid = to_regclass(:name)
            """
        ),
        {"name": name},
    ).one_or_none()
    if row is None:
        return False, False, None
    predicate = None if row.predicate is None else str(row.predicate)
    return True, bool(row.indisvalid), predicate


def _replace_skill_active_index() -> None:
    """Swap the archived-aware index without blocking normal table writes.

    The cutover migration has already copied archived rows to deleted_at and
    prevents new NULL slugs. The temporary name makes this restart-safe if a
    concurrent operation commits before a later contract statement fails.
    """
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        op.execute(sa.text("SET lock_timeout = '1s'"))
        canonical_exists, canonical_valid, canonical_predicate = _index_state(
            bind, SKILL_ACTIVE_INDEX
        )
        contract_exists, contract_valid, _ = _index_state(
            bind, SKILL_ACTIVE_INDEX_CONTRACT
        )

        if canonical_valid and "archived_at" not in (canonical_predicate or ""):
            if contract_exists:
                op.execute(
                    sa.text(
                        f"DROP INDEX CONCURRENTLY IF EXISTS {SKILL_ACTIVE_INDEX_CONTRACT}"
                    )
                )
            op.execute(sa.text("RESET lock_timeout"))
            return

        if contract_exists and not contract_valid:
            op.execute(
                sa.text(
                    f"DROP INDEX CONCURRENTLY IF EXISTS {SKILL_ACTIVE_INDEX_CONTRACT}"
                )
            )
            contract_exists = False

        if not contract_exists:
            op.execute(
                sa.text(
                    f"CREATE UNIQUE INDEX CONCURRENTLY {SKILL_ACTIVE_INDEX_CONTRACT} "
                    "ON skill (workspace_id, slug) WHERE deleted_at IS NULL"
                )
            )

        if canonical_exists:
            op.execute(
                sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {SKILL_ACTIVE_INDEX}")
            )
        op.execute(
            sa.text(
                f"ALTER INDEX {SKILL_ACTIVE_INDEX_CONTRACT} "
                f"RENAME TO {SKILL_ACTIVE_INDEX}"
            )
        )
        op.execute(sa.text("RESET lock_timeout"))


def _contract_skills() -> None:
    op.alter_column(
        "skill",
        "slug",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_constraint(
        op.f(SKILL_SLUG_NOT_NULL_CONSTRAINT),
        "skill",
        type_="check",
    )
    op.drop_column("skill", "archived_at")


def _drop_legacy_preset_head() -> None:
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
    _replace_skill_active_index()
    op.execute(sa.text("SET LOCAL lock_timeout = '1s'"))
    op.alter_column(
        "agent_preset_version",
        "subagents_enabled",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.text("false"),
    )
    op.drop_constraint(
        op.f(SUBAGENTS_NOT_NULL_CONSTRAINT),
        "agent_preset_version",
        type_="check",
    )
    _drop_legacy_preset_head()
    _drop_legacy_version_fields()
    _contract_skills()


def downgrade() -> None:
    raise NotImplementedError(
        "Contracted preset snapshots cannot be reconstructed without inventing "
        "historical data; roll back the application to the cutover release instead."
    )
