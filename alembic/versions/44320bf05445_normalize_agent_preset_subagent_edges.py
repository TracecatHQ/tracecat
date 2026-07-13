"""Expand version-owned agent preset ResourceHead edges.

Revision ID: 44320bf05445
Revises: c6a8d4f3b2e1
Create Date: 2026-07-10 11:22:07.877453

This is the expand release. Existing application versions keep using the
legacy JSON and pinned SkillVersion columns. The expand application dual-writes
the nullable epoch marker and normalized version edges; a NULL marker means a
late legacy writer still owns that row.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

revision: str = "44320bf05445"
down_revision: str | None = "c6a8d4f3b2e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_version_subagent_table() -> None:
    op.create_unique_constraint(
        "uq_agent_preset_workspace_id_id",
        "agent_preset",
        ["workspace_id", "id"],
    )
    op.create_unique_constraint(
        "uq_agent_preset_version_workspace_id_id",
        "agent_preset_version",
        ["workspace_id", "id"],
    )
    op.create_table(
        "agent_preset_version_subagent",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("parent_preset_version_id", sa.UUID(), nullable=False),
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
            name=op.f("ck_agent_preset_version_subagent_max_turns_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "child_preset_id"],
            ["agent_preset.workspace_id", "agent_preset.id"],
            name="fk_agent_preset_version_subagent_workspace_child_agent_preset",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "parent_preset_version_id"],
            ["agent_preset_version.workspace_id", "agent_preset_version.id"],
            name="fk_ap_version_subagent_workspace_parent_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_preset_version_subagent_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_agent_preset_version_subagent")
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "parent_preset_version_id",
            "alias",
            name="uq_agent_preset_version_subagent_workspace_parent_alias",
        ),
    )
    op.create_index(
        op.f("ix_agent_preset_version_subagent_child_preset_id"),
        "agent_preset_version_subagent",
        ["child_preset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_preset_version_subagent_id"),
        "agent_preset_version_subagent",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_agent_preset_version_subagent_parent_preset_version_id"),
        "agent_preset_version_subagent",
        ["parent_preset_version_id"],
        unique=False,
    )


def _backfill_version_edges() -> None:
    op.execute(
        sa.text(
            """
            CREATE TEMP TABLE _agent_preset_version_subagent_backfill
            ON COMMIT DROP AS
            WITH refs AS (
                SELECT
                    parent.id AS parent_version_id,
                    parent.workspace_id,
                    ref.value AS ref
                FROM agent_preset_version AS parent
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof(parent.agents -> 'subagents') = 'array'
                        THEN parent.agents -> 'subagents'
                        ELSE '[]'::jsonb
                    END
                ) AS ref(value)
            )
            SELECT
                refs.parent_version_id,
                refs.workspace_id,
                CASE
                    WHEN refs.ref ->> 'preset_id' IS NOT NULL THEN child_by_id.id
                    WHEN child_by_slug.active_count = 1 THEN child_by_slug.child_id
                    WHEN child_by_slug.active_count = 0
                        AND child_by_slug.total_count = 1
                        THEN child_by_slug.child_id
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
                AND child_by_id.id = CASE
                    WHEN refs.ref ->> 'preset_id' ~*
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                    THEN (refs.ref ->> 'preset_id')::uuid
                    ELSE NULL
                END
            LEFT JOIN LATERAL (
                SELECT
                    (
                        array_agg(
                            candidate.id
                            ORDER BY
                                (candidate.deleted_at IS NULL) DESC,
                                candidate.id
                        )
                    )[1] AS child_id,
                    count(*) FILTER (WHERE candidate.deleted_at IS NULL)
                        AS active_count,
                    count(*) AS total_count
                FROM agent_preset AS candidate
                WHERE candidate.workspace_id = refs.workspace_id
                    AND candidate.slug = refs.ref ->> 'preset'
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
                    FROM _agent_preset_version_subagent_backfill
                    WHERE child_id IS NULL OR alias IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize preset version subagents: unresolved or cross-workspace reference';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM _agent_preset_version_subagent_backfill
                    GROUP BY workspace_id, parent_version_id, alias
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize preset version subagents: duplicate alias';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM _agent_preset_version_subagent_backfill AS edge
                    JOIN agent_preset_version AS version
                        ON version.id = edge.parent_version_id
                    WHERE version.agents -> 'enabled'
                        IS DISTINCT FROM 'true'::jsonb
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize preset version subagents: disabled config has children';
                END IF;
            END $$;

            INSERT INTO agent_preset_version_subagent (
                id,
                parent_preset_version_id,
                child_preset_id,
                alias,
                description,
                max_turns,
                workspace_id
            )
            SELECT
                gen_random_uuid(),
                parent_version_id,
                child_id,
                alias,
                description,
                max_turns,
                workspace_id
            FROM _agent_preset_version_subagent_backfill
            ORDER BY parent_version_id, alias;

            UPDATE agent_preset_version
            SET subagents_enabled = CASE
                WHEN agents -> 'enabled' = 'true'::jsonb THEN true
                WHEN agents -> 'enabled' = 'false'::jsonb THEN false
                ELSE false
            END;
            """
        )
    )


def upgrade() -> None:
    # Cutover application versions create metadata-only ResourceHead rows.
    # Keep the legacy columns for rollback, but stop requiring new writers to
    # populate execution data in both places.
    for column in ("model_name", "model_provider"):
        op.alter_column(
            "agent_preset",
            column,
            existing_type=sa.String(length=120),
            nullable=True,
        )
    op.add_column(
        "agent_preset_version",
        sa.Column("subagents_enabled", sa.Boolean(), nullable=True),
    )
    for table in ("agent_preset_skill", "agent_preset_version_skill"):
        op.alter_column(
            table,
            "skill_version_id",
            existing_type=sa.UUID(),
            nullable=True,
        )
    _create_version_subagent_table()
    _backfill_version_edges()
    op.execute(enable_workspace_table_rls("agent_preset_version_subagent"))


def downgrade() -> None:
    skill = sa.table(
        "skill",
        sa.column("id", sa.UUID()),
        sa.column("workspace_id", sa.UUID()),
        sa.column("current_version_id", sa.UUID()),
    )
    for table in ("agent_preset_skill", "agent_preset_version_skill"):
        binding = sa.table(
            table,
            sa.column("skill_id", sa.UUID()),
            sa.column("skill_version_id", sa.UUID()),
            sa.column("workspace_id", sa.UUID()),
        )
        op.execute(
            binding.update()
            .where(binding.c.skill_version_id.is_(None))
            .values(
                skill_version_id=sa.select(skill.c.current_version_id)
                .where(
                    skill.c.id == binding.c.skill_id,
                    skill.c.workspace_id == binding.c.workspace_id,
                )
                .scalar_subquery()
            )
        )
        op.alter_column(
            table,
            "skill_version_id",
            existing_type=sa.UUID(),
            nullable=False,
        )

    op.execute(disable_workspace_table_rls("agent_preset_version_subagent"))
    op.drop_index(
        op.f("ix_agent_preset_version_subagent_parent_preset_version_id"),
        table_name="agent_preset_version_subagent",
    )
    op.drop_index(
        op.f("ix_agent_preset_version_subagent_id"),
        table_name="agent_preset_version_subagent",
    )
    op.drop_index(
        op.f("ix_agent_preset_version_subagent_child_preset_id"),
        table_name="agent_preset_version_subagent",
    )
    op.drop_table("agent_preset_version_subagent")
    op.drop_constraint(
        "uq_agent_preset_version_workspace_id_id",
        "agent_preset_version",
        type_="unique",
    )
    op.drop_constraint(
        "uq_agent_preset_workspace_id_id",
        "agent_preset",
        type_="unique",
    )
    op.drop_column("agent_preset_version", "subagents_enabled")
    for column in ("model_name", "model_provider"):
        op.execute(
            sa.text(
                f"""
                UPDATE agent_preset AS preset
                SET {column} = version.{column}
                FROM agent_preset_version AS version
                WHERE preset.{column} IS NULL
                  AND preset.current_version_id = version.id
                  AND preset.workspace_id = version.workspace_id
                """
            )
        )
        op.alter_column(
            "agent_preset",
            column,
            existing_type=sa.String(length=120),
            nullable=False,
        )
