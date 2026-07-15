"""Cut over agent preset ResourceHead ownership.

Revision ID: d2e4f6a8b0c1
Revises: 44320bf05445
Create Date: 2026-07-15 00:00:00.000000

Reconcile rows written during the expand window, make normalized version edges
authoritative, and keep expand and cutover writers compatible during rollout.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2e4f6a8b0c1"
down_revision: str | None = "44320bf05445"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SKILL_SLUG_INDEX = "uq_skill_workspace_slug_active"
SUBAGENTS_NOT_NULL_CONSTRAINT = "ck_agent_preset_version_subagents_enabled_not_null"
SUBAGENTS_SYNC_FUNCTION = "sync_agent_preset_version_subagents_enabled"
SUBAGENTS_SYNC_TRIGGER = "trg_agent_preset_version_subagents_enabled"

_SKILL = sa.table(
    "skill",
    sa.column("deleted_at", sa.TIMESTAMP(timezone=True)),
    sa.column("archived_at", sa.TIMESTAMP(timezone=True)),
)


def _reconcile_version_subagents() -> None:
    """Replace expand-window edges from their exact legacy JSON projection."""

    # JSONB expansion and per-row LATERAL resolution are PostgreSQL-specific.
    # The named CTEs document the resolution and validation stages directly.
    op.execute(
        sa.text(
            """
            CREATE TEMP TABLE _cutover_agent_preset_version_subagents
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
            ) AS child_by_slug ON TRUE;

            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM _cutover_agent_preset_version_subagents
                    WHERE child_id IS NULL OR alias IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'Cannot cut over preset version subagents: unresolved or cross-workspace reference';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM _cutover_agent_preset_version_subagents
                    GROUP BY workspace_id, parent_version_id, alias
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot cut over preset version subagents: duplicate alias';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM _cutover_agent_preset_version_subagents AS edge
                    JOIN agent_preset_version AS version
                        ON version.id = edge.parent_version_id
                    WHERE version.agents -> 'enabled'
                        IS DISTINCT FROM 'true'::jsonb
                ) THEN
                    RAISE EXCEPTION
                        'Cannot cut over preset version subagents: disabled config has children';
                END IF;
            END $$;

            DELETE FROM agent_preset_version_subagent;

            INSERT INTO agent_preset_version_subagent (
                parent_preset_version_id,
                child_preset_id,
                alias,
                description,
                max_turns,
                workspace_id
            )
            SELECT
                parent_version_id,
                child_id,
                alias,
                description,
                max_turns,
                workspace_id
            FROM _cutover_agent_preset_version_subagents
            ORDER BY parent_version_id, alias;

            UPDATE agent_preset_version
            SET subagents_enabled = COALESCE(
                (agents ->> 'enabled')::boolean,
                false
            );
            """
        )
    )


def _install_expand_writer_bridge() -> None:
    """Populate the new ownership bit for expand writers during rollout."""

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {SUBAGENTS_SYNC_FUNCTION}()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'INSERT' AND NEW.subagents_enabled IS NULL THEN
                    NEW.subagents_enabled := COALESCE(
                        (NEW.agents ->> 'enabled')::boolean,
                        false
                    );
                ELSIF TG_OP = 'UPDATE'
                    AND NEW.agents IS DISTINCT FROM OLD.agents
                    AND NEW.subagents_enabled IS NOT DISTINCT FROM OLD.subagents_enabled
                THEN
                    NEW.subagents_enabled := COALESCE(
                        (NEW.agents ->> 'enabled')::boolean,
                        false
                    );
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER {SUBAGENTS_SYNC_TRIGGER}
            BEFORE INSERT OR UPDATE OF agents
            ON agent_preset_version
            FOR EACH ROW
            EXECUTE FUNCTION {SUBAGENTS_SYNC_FUNCTION}();
            """
        )
    )


def _drop_expand_writer_bridge() -> None:
    op.execute(
        sa.text(
            f"""
            DROP TRIGGER IF EXISTS {SUBAGENTS_SYNC_TRIGGER}
            ON agent_preset_version;
            DROP FUNCTION IF EXISTS {SUBAGENTS_SYNC_FUNCTION}();
            """
        )
    )


def _replace_skill_slug_index(*, include_archived: bool) -> None:
    op.drop_index(SKILL_SLUG_INDEX, table_name="skill")
    predicate = (
        "deleted_at IS NULL AND archived_at IS NULL"
        if include_archived
        else "deleted_at IS NULL"
    )
    op.create_index(
        SKILL_SLUG_INDEX,
        "skill",
        ["workspace_id", "slug"],
        unique=True,
        postgresql_where=sa.text(predicate),
    )


def upgrade() -> None:
    op.add_column(
        "agent_preset_version",
        sa.Column("subagents_enabled", sa.Boolean(), nullable=True),
    )
    _reconcile_version_subagents()

    op.execute(
        _SKILL.update()
        .where(_SKILL.c.deleted_at.is_(None), _SKILL.c.archived_at.is_not(None))
        .values(deleted_at=_SKILL.c.archived_at)
    )
    _replace_skill_slug_index(include_archived=False)

    op.execute(
        sa.text(
            "ALTER TABLE agent_preset_version ADD CONSTRAINT "
            f"{SUBAGENTS_NOT_NULL_CONSTRAINT} "
            "CHECK (subagents_enabled IS NOT NULL) NOT VALID"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE agent_preset_version VALIDATE CONSTRAINT "
            f"{SUBAGENTS_NOT_NULL_CONSTRAINT}"
        )
    )
    _install_expand_writer_bridge()


def downgrade() -> None:
    _drop_expand_writer_bridge()
    op.drop_constraint(
        SUBAGENTS_NOT_NULL_CONSTRAINT,
        "agent_preset_version",
        type_="check",
    )
    op.drop_column("agent_preset_version", "subagents_enabled")
    _replace_skill_slug_index(include_archived=True)
