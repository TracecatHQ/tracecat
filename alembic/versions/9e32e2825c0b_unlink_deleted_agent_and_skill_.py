"""Unlink previously deleted agents and skills from all saved dependencies.

Revision ID: 9e32e2825c0b
Revises: c3a17be4d902
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9e32e2825c0b"
down_revision: str | None = "c3a17be4d902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
    # Intentionally irreversible: deletion permanently removes these links.
    pass
