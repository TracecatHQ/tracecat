"""Expand ResourceHead ownership.

Revision ID: 44320bf05445
Revises: c6a8d4f3b2e1
Create Date: 2026-07-10 11:22:07.877453

This is the expand release. Existing application versions keep using the
legacy JSON and pinned SkillVersion columns. The expand application dual-writes
the nullable epoch marker and normalized version edges; a NULL marker means a
late legacy writer still owns that row.

Skill slugs are the exception to the expand-only shape: the b51 application
already writes a slug on every Skill insert. This revision closes residual
slug-less rows from the earlier nullable migration and enforces NOT NULL.
"""

import logging
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

revision: str = "44320bf05445"
down_revision: str | None = "c6a8d4f3b2e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")
SKILL_SLUG_MAX_LENGTH = 64


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
        sa.Column("parent_preset_version_id", sa.UUID(), nullable=False),
        sa.Column("child_preset_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.String(length=160), nullable=False),
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
        op.f("ix_agent_preset_version_subagent_parent_preset_version_id"),
        "agent_preset_version_subagent",
        ["parent_preset_version_id"],
        unique=False,
    )


def _backfill_version_edges() -> None:
    op.execute(
        sa.text(
            """
            -- Materialize the complete legacy-to-normalized mapping before
            -- validating or writing any durable edge rows. The temp table is
            -- transaction-local and disappears when the migration commits.
            CREATE TEMP TABLE _agent_preset_version_subagent_backfill
            ON COMMIT DROP AS
            WITH refs AS (
                -- Expand each version's legacy JSON subagent array into one
                -- candidate edge per element. Missing or malformed arrays are
                -- treated as empty so versions without subagents need no row.
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
                    -- Explicit ids take precedence over slugs. If an id was
                    -- supplied but cannot be resolved, leave the edge NULL so
                    -- the validation block below aborts instead of silently
                    -- falling back to a possibly different slug target.
                    WHEN refs.ref ->> 'preset_id' IS NOT NULL THEN child_by_id.id
                    -- A slug is safe when it identifies exactly one active
                    -- head. A single tombstone is also preserved so historical
                    -- version edges continue to identify their deleted target.
                    WHEN child_by_slug.active_count = 1 THEN child_by_slug.child_id
                    WHEN child_by_slug.active_count = 0
                        AND child_by_slug.total_count = 1
                        THEN child_by_slug.child_id
                    ELSE NULL
                END AS child_id,
                -- Legacy bindings used `name` as the invocation alias and
                -- otherwise defaulted it to the referenced preset slug.
                COALESCE(NULLIF(refs.ref ->> 'name', ''), refs.ref ->> 'preset')
                    AS alias,
                refs.ref ->> 'description' AS description,
                CASE
                    WHEN jsonb_typeof(refs.ref -> 'max_turns') = 'number'
                    THEN (refs.ref ->> 'max_turns')::integer
                    ELSE NULL
                END AS max_turns
            FROM refs
            -- Guard the UUID cast with the accepted UUID shape. Invalid ids
            -- remain unresolved and are reported by the validation block.
            LEFT JOIN agent_preset AS child_by_id
                ON child_by_id.workspace_id = refs.workspace_id
                AND child_by_id.id = CASE
                    WHEN refs.ref ->> 'preset_id' ~*
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                    THEN (refs.ref ->> 'preset_id')::uuid
                    ELSE NULL
                END
            LEFT JOIN LATERAL (
                -- Resolve slug references inside the parent's workspace while
                -- retaining counts needed to reject ambiguous active or
                -- tombstoned matches. Ordering makes the selected candidate
                -- deterministic and prefers an active head when one exists.
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
                -- Every legacy entry must resolve to a same-workspace child and
                -- a non-NULL effective alias before normalized rows are added.
                IF EXISTS (
                    SELECT 1
                    FROM _agent_preset_version_subagent_backfill
                    WHERE child_id IS NULL OR alias IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize preset version subagents: unresolved or cross-workspace reference';
                END IF;
                -- The normalized table enforces alias uniqueness per parent
                -- version, so surface conflicting legacy JSON explicitly.
                IF EXISTS (
                    SELECT 1
                    FROM _agent_preset_version_subagent_backfill
                    GROUP BY workspace_id, parent_version_id, alias
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize preset version subagents: duplicate alias';
                END IF;
                -- A disabled legacy config must not contain child bindings. Do
                -- not normalize inconsistent state that the new model forbids.
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

            -- Validation has succeeded for the whole dataset; copy the staged
            -- mappings into the durable normalized edge table.
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
            FROM _agent_preset_version_subagent_backfill
            ORDER BY parent_version_id, alias;
            """
        )
    )


def _suffixed_skill_slug(slug: str, counter: int) -> str:
    suffix = f"-{counter}"
    return f"{slug[: SKILL_SLUG_MAX_LENGTH - len(suffix)]}{suffix}"


def _report_skill_slug_rename(row: dict[str, Any], counter: int) -> None:
    # Slugs derive from customer-authored names, so report identifiers only.
    message = (
        "Assigned a suffixed slug to an expand-window skill row: "
        f"workspace_id={row['workspace_id']} "
        f"skill_id={row['id']} "
        f"suffix_counter={counter}"
    )
    print(message)
    logger.info(message)


def _contract_skill_slugs(bind: Connection) -> None:
    # Keep the live-row set stable while reserving collision-free slugs. Reads
    # remain available, but concurrent Skill inserts and updates wait until the
    # migration transaction has applied NOT NULL.
    bind.execute(sa.text("LOCK TABLE skill IN SHARE ROW EXCLUSIVE MODE"))

    live_predicate = "deleted_at IS NULL AND archived_at IS NULL"
    occupied_rows = (
        bind.execute(
            sa.text(
                f"""
                SELECT workspace_id, slug
                FROM skill
                WHERE {live_predicate} AND slug IS NOT NULL
                """
            )
        )
        .mappings()
        .all()
    )
    occupied_by_workspace: dict[Any, set[str]] = {}
    for row in occupied_rows:
        occupied_by_workspace.setdefault(row["workspace_id"], set()).add(row["slug"])

    # Assign each late live row against both existing slugs and candidates
    # reserved earlier in this pass. Ordering makes collision resolution stable.
    missing_live_rows = (
        bind.execute(
            sa.text(
                f"""
                SELECT id, workspace_id, name
                FROM skill
                WHERE {live_predicate} AND slug IS NULL
                ORDER BY workspace_id, created_at, id
                """
            )
        )
        .mappings()
        .all()
    )
    for row in missing_live_rows:
        occupied = occupied_by_workspace.setdefault(row["workspace_id"], set())
        slug = row["name"]
        counter = 1
        while slug in occupied:
            counter += 1
            slug = _suffixed_skill_slug(row["name"], counter)
        bind.execute(
            sa.text("UPDATE skill SET slug = :slug WHERE id = :id"),
            {"id": row["id"], "slug": slug},
        )
        occupied.add(slug)
        if counter > 1:
            _report_skill_slug_rename(dict(row), counter)

    # Deleted rows do not participate in the live unique index, but they still
    # need a value before the column can become NOT NULL.
    bind.execute(
        sa.text(
            """
            UPDATE skill
            SET slug = name
            WHERE slug IS NULL
            """
        )
    )

    missing_count = bind.execute(
        sa.text("SELECT count(*) FROM skill WHERE slug IS NULL")
    ).scalar_one()
    if missing_count:
        raise RuntimeError(
            "Skill slug migration left NULL rows before SET NOT NULL: "
            f"count={missing_count}"
        )

    op.alter_column(
        "skill",
        "slug",
        existing_type=sa.String(length=64),
        nullable=False,
    )


def upgrade() -> None:
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
    _contract_skill_slugs(op.get_bind())


def downgrade() -> None:
    op.alter_column(
        "skill",
        "slug",
        existing_type=sa.String(length=64),
        nullable=True,
    )

    # Fail before changing any schema or data when the old representation cannot
    # be reconstructed. Operators must publish the referenced heads first.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                -- The old schema requires every skill binding to pin a concrete
                -- version. A NULL normalized edge can only be reconstructed if
                -- its referenced ResourceHead currently publishes a version.
                IF EXISTS (
                    SELECT 1
                    FROM agent_preset_skill AS binding
                    LEFT JOIN skill
                        ON skill.workspace_id = binding.workspace_id
                        AND skill.id = binding.skill_id
                    WHERE binding.skill_version_id IS NULL
                        AND skill.current_version_id IS NULL
                    UNION ALL
                    SELECT 1
                    FROM agent_preset_version_skill AS binding
                    LEFT JOIN skill
                        ON skill.workspace_id = binding.workspace_id
                        AND skill.id = binding.skill_id
                    WHERE binding.skill_version_id IS NULL
                        AND skill.current_version_id IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'Cannot downgrade ResourceHead skill edges: publish every referenced skill before retrying';
                END IF;

            END $$;
            """
        )
    )

    skill = sa.table(
        "skill",
        sa.column("id", sa.UUID()),
        sa.column("workspace_id", sa.UUID()),
        sa.column("current_version_id", sa.UUID()),
    )
    for table in ("agent_preset_skill", "agent_preset_version_skill"):
        # Re-pin normalized ResourceHead bindings to each head's current version
        # before restoring the old NOT NULL constraint.
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
