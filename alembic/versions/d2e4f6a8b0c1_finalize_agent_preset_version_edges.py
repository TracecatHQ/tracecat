"""Finalize version-owned agent preset ResourceHead edges.

Revision ID: d2e4f6a8b0c1
Revises: b4e6c8a2d0f1
Create Date: 2026-07-13 00:00:00.000000

This is the cutover barrier. It reconciles rows written by legacy tasks during
the expand rollout, then closes the nullable representation epoch before the
application stops reading legacy JSON.
"""

import logging
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op

revision: str = "d2e4f6a8b0c1"
down_revision: str | None = "b4e6c8a2d0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")
SKILL_SLUG_MAX_LENGTH = 64
SUBAGENTS_NOT_NULL_CONSTRAINT = "ck_agent_preset_version_subagents_enabled_not_null"
SKILL_SLUG_NOT_NULL_CONSTRAINT = "ck_skill_slug_not_null"


skill = sa.table(
    "skill",
    sa.column("id", sa.UUID()),
    sa.column("workspace_id", sa.UUID()),
    sa.column("name", sa.String(length=SKILL_SLUG_MAX_LENGTH)),
    sa.column("slug", sa.String(length=SKILL_SLUG_MAX_LENGTH)),
    sa.column("created_at", sa.TIMESTAMP(timezone=True)),
    sa.column("archived_at", sa.TIMESTAMP(timezone=True)),
    sa.column("deleted_at", sa.TIMESTAMP(timezone=True)),
)


def _suffixed_skill_slug(slug: str, counter: int) -> str:
    suffix = f"-{counter}"
    return f"{slug[: SKILL_SLUG_MAX_LENGTH - len(suffix)]}{suffix}"


def _reconcile_late_legacy_skills(bind: Connection) -> None:
    """Close rows written by legacy tasks after the expand backfill.

    The expand index already excludes archived rows and enforces uniqueness for
    live non-NULL slugs. Assigning late NULL slugs one row at a time preserves
    that index throughout the cutover instead of dropping it around a bulk
    backfill.
    """

    bind.execute(
        skill.update()
        .where(skill.c.deleted_at.is_(None), skill.c.archived_at.is_not(None))
        .values(deleted_at=skill.c.archived_at)
    )

    used_rows = bind.execute(
        sa.select(skill.c.workspace_id, skill.c.slug).where(
            skill.c.deleted_at.is_(None),
            skill.c.slug.is_not(None),
        )
    )
    used_by_workspace: dict[Any, set[str]] = {}
    for workspace_id, slug in used_rows.tuples():
        used_by_workspace.setdefault(workspace_id, set()).add(slug)

    late_live_rows = (
        bind.execute(
            sa.select(
                skill.c.id,
                skill.c.workspace_id,
                skill.c.name,
            )
            .where(
                skill.c.deleted_at.is_(None),
                skill.c.slug.is_(None),
            )
            .order_by(skill.c.workspace_id, skill.c.created_at, skill.c.id)
        )
        .mappings()
        .all()
    )
    for row in late_live_rows:
        workspace_id = row["workspace_id"]
        base_slug = row["name"]
        used = used_by_workspace.setdefault(workspace_id, set())
        candidate = base_slug
        counter = 1
        while candidate in used:
            counter += 1
            candidate = _suffixed_skill_slug(base_slug, counter)
        bind.execute(
            skill.update().where(skill.c.id == row["id"]).values(slug=candidate)
        )
        used.add(candidate)
        if counter > 1:
            logger.info(
                "Renamed late live skill slug collision during cutover: "
                "workspace_id=%s skill_id=%s suffix_counter=%s",
                workspace_id,
                row["id"],
                counter,
            )

    bind.execute(skill.update().where(skill.c.slug.is_(None)).values(slug=skill.c.name))


def _reconcile_late_legacy_versions() -> None:
    op.execute(
        sa.text(
            """
            CREATE TEMP TABLE _late_agent_preset_version_subagents
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
                WHERE parent.subagents_enabled IS NULL
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
                    FROM _late_agent_preset_version_subagents
                    WHERE child_id IS NULL OR alias IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'Cannot cut over preset version subagents: unresolved or cross-workspace reference';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM _late_agent_preset_version_subagents
                    GROUP BY workspace_id, parent_version_id, alias
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot cut over preset version subagents: duplicate alias';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM _late_agent_preset_version_subagents AS edge
                    JOIN agent_preset_version AS version
                        ON version.id = edge.parent_version_id
                    WHERE version.agents -> 'enabled'
                        IS DISTINCT FROM 'true'::jsonb
                ) THEN
                    RAISE EXCEPTION
                        'Cannot cut over preset version subagents: disabled config has children';
                END IF;
            END $$;

            DELETE FROM agent_preset_version_subagent AS edge
            USING agent_preset_version AS version
            WHERE edge.parent_preset_version_id = version.id
              AND version.subagents_enabled IS NULL;

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
            FROM _late_agent_preset_version_subagents
            ORDER BY parent_version_id, alias;

            UPDATE agent_preset_version
            SET subagents_enabled = CASE
                WHEN agents -> 'enabled' = 'true'::jsonb THEN true
                WHEN agents -> 'enabled' = 'false'::jsonb THEN false
                ELSE false
            END
            WHERE subagents_enabled IS NULL;
            """
        )
    )


def upgrade() -> None:
    _reconcile_late_legacy_versions()
    _reconcile_late_legacy_skills(op.get_bind())
    op.execute(
        sa.text(
            "ALTER TABLE skill ADD CONSTRAINT "
            f"{SKILL_SLUG_NOT_NULL_CONSTRAINT} "
            "CHECK (slug IS NOT NULL) NOT VALID"
        )
    )
    op.execute(
        sa.text(
            f"ALTER TABLE skill VALIDATE CONSTRAINT {SKILL_SLUG_NOT_NULL_CONSTRAINT}"
        )
    )
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


def downgrade() -> None:
    op.drop_constraint(
        op.f(SKILL_SLUG_NOT_NULL_CONSTRAINT),
        "skill",
        type_="check",
    )
    op.drop_constraint(
        op.f(SUBAGENTS_NOT_NULL_CONSTRAINT),
        "agent_preset_version",
        type_="check",
    )
