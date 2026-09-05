"""Default agents config to empty subagents

Revision ID: c3a17be4d902
Revises: 44d7e75b6f4c
Create Date: 2026-09-04 12:00:00.000000

This is the expand half of an expand/contract pair. The legacy ``enabled`` key
is deliberately left on rows written before this release: migrations run ahead
of the application rollout, and the old app version reads a missing ``enabled``
as ``False``, which trips its ``subagents require enabled=true`` validator and
silently drops Agent-tool access. The new app strips the key on read. A later
contract migration removes it from stored rows once the old replicas are gone.

The column default changes safely because both app versions set ``agents``
explicitly on insert. Existing references to deleted agents and Skills are
also removed from current and saved versions; this unlinking is permanent.
The cleanup preserves the legacy ``enabled`` key and all other configuration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3a17be4d902"
down_revision: str | None = "44d7e75b6f4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_AGENTS_TABLES = ("agent_preset", "agent_preset_version")
_AGENTS_TYPE = postgresql.JSONB(astext_type=sa.Text())
_EMPTY_SUBAGENTS_SQL = sa.text("'{\"subagents\": []}'::jsonb")


def upgrade() -> None:
    for table in _AGENTS_TABLES:
        op.alter_column(
            table,
            "agents",
            existing_type=_AGENTS_TYPE,
            existing_nullable=False,
            server_default=_EMPTY_SUBAGENTS_SQL,
        )

    # Data-only migration: schema autogeneration cannot infer this cleanup.
    # Match UUIDs, never recycled slugs; preserve all other configuration/order.
    for table in ("agent_preset", "agent_preset_version"):
        op.execute(
            sa.text(f"""
            UPDATE {table} AS parent
            SET agents = jsonb_set(parent.agents, '{{subagents}}', (
                SELECT COALESCE(jsonb_agg(ref ORDER BY ordinal), '[]'::jsonb)
                FROM jsonb_array_elements(parent.agents->'subagents')
                     WITH ORDINALITY AS refs(ref, ordinal)
                WHERE NOT EXISTS (
                    SELECT 1 FROM agent_preset AS child
                    WHERE child.workspace_id = parent.workspace_id
                      AND child.id::text = ref->>'preset_id'
                      AND child.deleted_at IS NOT NULL
                )
            ))
            WHERE EXISTS (
                SELECT 1
                FROM jsonb_array_elements(parent.agents->'subagents') AS refs(ref)
                JOIN agent_preset AS child
                  ON child.id::text = ref->>'preset_id'
                 AND child.workspace_id = parent.workspace_id
                WHERE child.deleted_at IS NOT NULL
            )
        """)
        )
    for table in ("agent_preset_skill", "agent_preset_version_skill"):
        op.execute(
            sa.text(f"""
            DELETE FROM {table} AS binding USING skill
            WHERE binding.workspace_id = skill.workspace_id
              AND binding.skill_id = skill.id
              AND (skill.deleted_at IS NOT NULL OR skill.archived_at IS NOT NULL)
        """)
        )


def downgrade() -> None:
    raise NotImplementedError(
        "Database downgrade is unsupported: deleted dependency links cannot be "
        "restored by this migration. Roll back the application only. Recover "
        "deleted database data from a backup or snapshot if needed."
    )
