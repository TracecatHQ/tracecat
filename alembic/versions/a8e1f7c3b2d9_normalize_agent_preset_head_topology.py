"""Normalize agent preset topology onto resource heads.

Revision ID: a8e1f7c3b2d9
Revises: 44d7e75b6f4c
Create Date: 2026-08-28 19:00:00.000000

The legacy topology projections remain in place for rollback compatibility.
New application code treats ``agent_preset_subagent`` and
``agent_preset_skill`` as the canonical head-owned topology.
"""

from collections.abc import Sequence

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


def _backfill_subagent_head_edges() -> None:
    """Project every live head's current effective version topology."""

    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM agent_preset AS preset
                    LEFT JOIN agent_preset_version AS current_version
                        ON current_version.id = preset.current_version_id
                    WHERE preset.deleted_at IS NULL
                        AND preset.current_version_id IS NOT NULL
                        AND (
                            current_version.id IS NULL
                            OR current_version.workspace_id <> preset.workspace_id
                            OR current_version.preset_id <> preset.id
                        )
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize agent preset topology: invalid current preset version';
                END IF;
            END $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TEMP TABLE _agent_preset_subagent_backfill
            ON COMMIT DROP AS
            WITH current_topology AS (
                SELECT
                    parent.id AS parent_id,
                    parent.workspace_id,
                    COALESCE(current_version.agents, parent.agents) AS agents
                FROM agent_preset AS parent
                LEFT JOIN agent_preset_version AS current_version
                    ON current_version.workspace_id = parent.workspace_id
                    AND current_version.id = parent.current_version_id
                WHERE parent.deleted_at IS NULL
            ),
            refs AS (
                SELECT
                    topology.parent_id,
                    topology.workspace_id,
                    ref.value AS ref
                FROM current_topology AS topology
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof(topology.agents -> 'subagents') = 'array'
                        THEN topology.agents -> 'subagents'
                        ELSE '[]'::jsonb
                    END
                ) AS ref(value)
            )
            SELECT
                refs.parent_id,
                refs.workspace_id,
                CASE
                    WHEN refs.ref ->> 'preset_id' IS NOT NULL THEN child_by_id.id
                    WHEN child_by_slug.match_count = 1 THEN child_by_slug.child_id
                    ELSE NULL
                END AS child_id,
                COALESCE(NULLIF(refs.ref ->> 'name', ''), refs.ref ->> 'preset')
                    AS alias,
                refs.ref ->> 'description' AS description,
                CASE
                    WHEN jsonb_typeof(refs.ref -> 'max_turns') = 'number'
                    THEN (refs.ref ->> 'max_turns')::integer
                    ELSE NULL
                END AS max_turns
            FROM refs
            LEFT JOIN agent_preset AS child_by_id
                ON child_by_id.workspace_id = refs.workspace_id
                AND child_by_id.deleted_at IS NULL
                AND child_by_id.id = CASE
                    WHEN refs.ref ->> 'preset_id' ~*
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                    THEN (refs.ref ->> 'preset_id')::uuid
                    ELSE NULL
                END
            LEFT JOIN LATERAL (
                SELECT
                    (array_agg(candidate.id ORDER BY candidate.id))[1] AS child_id,
                    count(*) AS match_count
                FROM agent_preset AS candidate
                WHERE candidate.workspace_id = refs.workspace_id
                    AND candidate.slug = refs.ref ->> 'preset'
                    AND candidate.deleted_at IS NULL
            ) AS child_by_slug ON TRUE
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM _agent_preset_subagent_backfill
                    WHERE child_id IS NULL OR alias IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize agent preset topology: unresolved or cross-workspace subagent head';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM _agent_preset_subagent_backfill
                    GROUP BY workspace_id, parent_id, alias
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize agent preset topology: duplicate subagent alias';
                END IF;
            END $$;

            INSERT INTO agent_preset_subagent (
                id,
                parent_preset_id,
                child_preset_id,
                alias,
                description,
                max_turns,
                workspace_id
            )
            SELECT
                gen_random_uuid(),
                parent_id,
                child_id,
                alias,
                description,
                max_turns,
                workspace_id
            FROM _agent_preset_subagent_backfill
            ORDER BY parent_id, alias
            """
        )
    )


def _reconcile_skill_head_edges() -> None:
    """Make existing head Skill edges match each head's current version."""

    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM agent_preset AS preset
                    JOIN agent_preset_version_skill AS version_edge
                        ON version_edge.preset_version_id = preset.current_version_id
                    LEFT JOIN skill
                        ON skill.id = version_edge.skill_id
                    LEFT JOIN skill_version
                        ON skill_version.id = version_edge.skill_version_id
                    WHERE preset.deleted_at IS NULL
                        AND (
                            version_edge.workspace_id <> preset.workspace_id
                            OR skill.id IS NULL
                            OR skill.workspace_id <> preset.workspace_id
                            OR skill.deleted_at IS NOT NULL
                            OR skill.archived_at IS NOT NULL
                            OR skill_version.id IS NULL
                            OR skill_version.workspace_id <> preset.workspace_id
                            OR skill_version.skill_id <> skill.id
                        )
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize agent preset topology: unresolved or cross-workspace skill head';
                END IF;
            END $$;

            DELETE FROM agent_preset_skill AS head_edge
            USING agent_preset AS preset
            WHERE preset.workspace_id = head_edge.workspace_id
                AND preset.id = head_edge.preset_id
                AND preset.deleted_at IS NULL
                AND preset.current_version_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM agent_preset_version_skill AS version_edge
                    WHERE version_edge.workspace_id = head_edge.workspace_id
                        AND version_edge.preset_version_id = preset.current_version_id
                        AND version_edge.skill_id = head_edge.skill_id
                );

            INSERT INTO agent_preset_skill (
                id,
                preset_id,
                skill_id,
                skill_version_id,
                workspace_id
            )
            SELECT
                gen_random_uuid(),
                preset.id,
                version_edge.skill_id,
                version_edge.skill_version_id,
                preset.workspace_id
            FROM agent_preset AS preset
            JOIN agent_preset_version_skill AS version_edge
                ON version_edge.workspace_id = preset.workspace_id
                AND version_edge.preset_version_id = preset.current_version_id
            WHERE preset.deleted_at IS NULL
            ON CONFLICT (workspace_id, preset_id, skill_id)
            DO UPDATE SET skill_version_id = EXCLUDED.skill_version_id
            """
        )
    )


def upgrade() -> None:
    # Freeze every legacy topology source and shadow target for the transaction.
    # This makes the set-based snapshot deterministic even if an old application
    # pod has not finished draining when the migration begins.
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
    _create_subagent_head_edges()
    _backfill_subagent_head_edges()
    _reconcile_skill_head_edges()
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
