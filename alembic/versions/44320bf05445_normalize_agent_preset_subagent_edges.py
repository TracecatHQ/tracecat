"""normalize agent preset subagent edges

Revision ID: 44320bf05445
Revises: c6a8d4f3b2e1
Create Date: 2026-07-10 11:22:07.877453

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "44320bf05445"
down_revision: str | None = "c6a8d4f3b2e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_binding_tables() -> None:
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
        "agent_preset_subagent",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("parent_preset_id", sa.UUID(), nullable=False),
        sa.Column("child_preset_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("max_turns", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "position >= 0",
            name=op.f("ck_agent_preset_subagent_position_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "child_preset_id"],
            ["agent_preset.workspace_id", "agent_preset.id"],
            name="fk_agent_preset_subagent_workspace_child_agent_preset",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "parent_preset_id"],
            ["agent_preset.workspace_id", "agent_preset.id"],
            name="fk_agent_preset_subagent_workspace_parent_agent_preset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_preset_subagent_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_preset_subagent")),
        sa.UniqueConstraint(
            "workspace_id",
            "parent_preset_id",
            "alias",
            name="uq_agent_preset_subagent_workspace_parent_alias",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "parent_preset_id",
            "position",
            name="uq_agent_preset_subagent_workspace_parent_position",
        ),
    )
    op.create_index(
        op.f("ix_agent_preset_subagent_child_preset_id"),
        "agent_preset_subagent",
        ["child_preset_id"],
        unique=False,
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

    op.create_table(
        "agent_preset_version_subagent",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("parent_preset_version_id", sa.UUID(), nullable=False),
        sa.Column("child_preset_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("max_turns", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "position >= 0",
            name=op.f("ck_agent_preset_version_subagent_position_nonnegative"),
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
        sa.UniqueConstraint(
            "workspace_id",
            "parent_preset_version_id",
            "position",
            name="uq_agent_preset_version_subagent_workspace_parent_position",
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


def _backfill_head_bindings() -> None:
    op.execute(
        sa.text(
            """
            CREATE TEMP TABLE _agent_preset_subagent_backfill
            ON COMMIT DROP AS
            WITH refs AS (
                SELECT
                    parent.id AS parent_id,
                    parent.workspace_id,
                    ref.value AS ref,
                    ref.ordinality - 1 AS position
                FROM agent_preset AS parent
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof(parent.agents -> 'subagents') = 'array'
                        THEN parent.agents -> 'subagents'
                        ELSE '[]'::jsonb
                    END
                ) WITH ORDINALITY AS ref(value, ordinality)
                -- Soft-deleted parents are unreachable at runtime and have no
                -- undelete path; their stale refs must not block the upgrade.
                WHERE parent.deleted_at IS NULL
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
                END AS max_turns,
                refs.position::integer AS position
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
                        'Cannot normalize agent preset subagents: unresolved or cross-workspace head reference';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM _agent_preset_subagent_backfill
                    GROUP BY workspace_id, parent_id, alias
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize agent preset subagents: duplicate alias on preset head';
                END IF;
            END $$;

            INSERT INTO agent_preset_subagent (
                id,
                parent_preset_id,
                child_preset_id,
                alias,
                description,
                max_turns,
                position,
                workspace_id
            )
            SELECT
                gen_random_uuid(),
                parent_id,
                child_id,
                alias,
                description,
                max_turns,
                position,
                workspace_id
            FROM _agent_preset_subagent_backfill
            ORDER BY parent_id, position;
            """
        )
    )


def _backfill_version_bindings() -> None:
    op.execute(
        sa.text(
            """
            CREATE TEMP TABLE _agent_preset_version_subagent_backfill
            ON COMMIT DROP AS
            WITH refs AS (
                SELECT
                    parent.id AS parent_version_id,
                    parent.workspace_id,
                    ref.value AS ref,
                    ref.ordinality - 1 AS position
                FROM agent_preset_version AS parent
                -- Versions owned by soft-deleted presets are unreachable at
                -- runtime and have no undelete path; skip their edges.
                JOIN agent_preset AS owner
                    ON owner.workspace_id = parent.workspace_id
                    AND owner.id = parent.preset_id
                    AND owner.deleted_at IS NULL
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof(parent.agents -> 'subagents') = 'array'
                        THEN parent.agents -> 'subagents'
                        ELSE '[]'::jsonb
                    END
                ) WITH ORDINALITY AS ref(value, ordinality)
            )
            SELECT
                refs.parent_version_id,
                refs.workspace_id,
                CASE
                    WHEN refs.ref ->> 'preset_id' IS NOT NULL THEN child_by_id.id
                    -- Prefer the active slug owner; a slug shared with
                    -- tombstones legitimately resolves to the active preset.
                    WHEN child_by_slug.active_match_count = 1
                        THEN child_by_slug.child_id
                    -- Historical version edges may point at a child that was
                    -- later soft-deleted; keep them instead of blocking the
                    -- upgrade. Runtime rejects such versions on restore.
                    WHEN child_by_slug.active_match_count = 0
                        AND child_by_slug.total_match_count = 1
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
                END AS max_turns,
                refs.position::integer AS position
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
                        AS active_match_count,
                    count(*) AS total_match_count
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
                        'Cannot normalize agent preset version subagents: unresolved or cross-workspace head reference';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM _agent_preset_version_subagent_backfill
                    GROUP BY workspace_id, parent_version_id, alias
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize agent preset version subagents: duplicate alias';
                END IF;
            END $$;

            INSERT INTO agent_preset_version_subagent (
                id,
                parent_preset_version_id,
                child_preset_id,
                alias,
                description,
                max_turns,
                position,
                workspace_id
            )
            SELECT
                gen_random_uuid(),
                parent_version_id,
                child_id,
                alias,
                description,
                max_turns,
                position,
                workspace_id
            FROM _agent_preset_version_subagent_backfill
            ORDER BY parent_version_id, position;
            """
        )
    )


def upgrade() -> None:
    op.add_column(
        "agent_preset",
        sa.Column(
            "subagents_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_preset_version",
        sa.Column(
            "subagents_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    _create_binding_tables()

    op.execute(
        sa.text(
            """
            UPDATE agent_preset
            SET subagents_enabled = CASE
                WHEN agents ->> 'enabled' IN ('true', 'false')
                THEN (agents ->> 'enabled')::boolean
                ELSE false
            END;

            UPDATE agent_preset_version
            SET subagents_enabled = CASE
                WHEN agents ->> 'enabled' IN ('true', 'false')
                THEN (agents ->> 'enabled')::boolean
                ELSE false
            END;
            """
        )
    )
    _backfill_head_bindings()
    _backfill_version_bindings()

    op.execute(enable_workspace_table_rls("agent_preset_subagent"))
    op.execute(enable_workspace_table_rls("agent_preset_version_subagent"))


def downgrade() -> None:
    op.execute(disable_workspace_table_rls("agent_preset_version_subagent"))
    op.execute(disable_workspace_table_rls("agent_preset_subagent"))

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

    op.drop_index(
        op.f("ix_agent_preset_subagent_parent_preset_id"),
        table_name="agent_preset_subagent",
    )
    op.drop_index(
        op.f("ix_agent_preset_subagent_id"),
        table_name="agent_preset_subagent",
    )
    op.drop_index(
        op.f("ix_agent_preset_subagent_child_preset_id"),
        table_name="agent_preset_subagent",
    )
    op.drop_table("agent_preset_subagent")

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
    op.drop_column("agent_preset", "subagents_enabled")
