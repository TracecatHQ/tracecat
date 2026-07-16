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
from sqlalchemy.dialects.postgresql import JSONB
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

_VERSION_SUBAGENT_BACKFILL = sa.table(
    "_agent_preset_version_subagent_backfill",
    sa.column("parent_version_id", sa.UUID()),
    sa.column("workspace_id", sa.UUID()),
    sa.column("child_id", sa.UUID()),
    sa.column("alias", sa.String(length=160)),
    sa.column("description", sa.String(length=1000)),
    sa.column("max_turns", sa.Integer()),
)
_AGENT_PRESET_VERSION = sa.table(
    "agent_preset_version",
    sa.column("id", sa.UUID()),
    sa.column("agents", JSONB()),
)
_AGENT_PRESET_VERSION_SUBAGENT = sa.table(
    "agent_preset_version_subagent",
    sa.column("parent_preset_version_id", sa.UUID()),
    sa.column("child_preset_id", sa.UUID()),
    sa.column("alias", sa.String(length=160)),
    sa.column("description", sa.String(length=1000)),
    sa.column("max_turns", sa.Integer()),
    sa.column("workspace_id", sa.UUID()),
)
_SKILL = sa.table(
    "skill",
    sa.column("id", sa.UUID()),
    sa.column("workspace_id", sa.UUID()),
    sa.column("name", sa.String(length=64)),
    sa.column("slug", sa.String(length=64)),
    sa.column("current_version_id", sa.UUID()),
    sa.column("created_at", sa.TIMESTAMP(timezone=True)),
    sa.column("deleted_at", sa.TIMESTAMP(timezone=True)),
    sa.column("archived_at", sa.TIMESTAMP(timezone=True)),
)


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
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
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
    """Stage, validate, then persist normalized edges from legacy JSON.

    The staging query applies these resolution rules to every legacy binding:

    1. An explicit ``preset_id`` resolves by id only; it never falls back to a slug.
    2. A slug resolves to its one active match, or to a lone deleted match.
    3. Missing and ambiguous targets remain NULL so validation rejects the batch.
    4. The invocation alias is ``name``, falling back to the referenced slug.

    Staging the full mapping first keeps validation atomic: no durable edge is
    inserted unless every legacy binding satisfies the normalized constraints.
    """
    # JSONB expansion and per-row LATERAL resolution are PostgreSQL-specific;
    # descriptive relation names keep that part clearer than a Core translation.
    op.execute(
        sa.text(
            """
            CREATE TEMP TABLE _agent_preset_version_subagent_backfill
            ON COMMIT DROP AS
            WITH legacy_bindings AS (
                SELECT
                    parent.id AS parent_version_id,
                    parent.workspace_id,
                    binding.value AS binding
                FROM agent_preset_version AS parent
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof(parent.agents -> 'subagents') = 'array'
                        THEN parent.agents -> 'subagents'
                        ELSE '[]'::jsonb
                    END
                ) AS binding(value)
            )
            SELECT
                legacy.parent_version_id,
                legacy.workspace_id,
                CASE
                    WHEN legacy.binding ->> 'preset_id' IS NOT NULL
                        THEN explicit_child.id
                    WHEN slug_match.active_count = 1
                        THEN slug_match.preferred_child_id
                    WHEN slug_match.active_count = 0
                        AND slug_match.total_count = 1
                        THEN slug_match.preferred_child_id
                    ELSE NULL
                END AS child_id,
                COALESCE(
                    NULLIF(legacy.binding ->> 'name', ''),
                    legacy.binding ->> 'preset'
                )
                    AS alias,
                legacy.binding ->> 'description' AS description,
                CASE
                    WHEN jsonb_typeof(legacy.binding -> 'max_turns') = 'number'
                    THEN (legacy.binding ->> 'max_turns')::integer
                    ELSE NULL
                END AS max_turns
            FROM legacy_bindings AS legacy
            LEFT JOIN agent_preset AS explicit_child
                ON explicit_child.workspace_id = legacy.workspace_id
                AND explicit_child.id = CASE
                    WHEN legacy.binding ->> 'preset_id' ~*
                        '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                    THEN (legacy.binding ->> 'preset_id')::uuid
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
                    )[1] AS preferred_child_id,
                    count(*) FILTER (WHERE candidate.deleted_at IS NULL)
                        AS active_count,
                    count(*) AS total_count
                FROM agent_preset AS candidate
                WHERE candidate.workspace_id = legacy.workspace_id
                    AND candidate.slug = legacy.binding ->> 'preset'
            ) AS slug_match ON TRUE
            """
        )
    )
    bind = op.get_bind()

    # Every legacy entry must resolve to a same-workspace child and a non-NULL
    # effective alias before normalized rows are added.
    unresolved = (
        sa.select(sa.literal(1))
        .select_from(_VERSION_SUBAGENT_BACKFILL)
        .where(
            sa.or_(
                _VERSION_SUBAGENT_BACKFILL.c.child_id.is_(None),
                _VERSION_SUBAGENT_BACKFILL.c.alias.is_(None),
            )
        )
        .limit(1)
    )
    if bind.execute(unresolved).first() is not None:
        raise RuntimeError(
            "Cannot normalize preset version subagents: "
            "unresolved or cross-workspace reference"
        )

    # The normalized table enforces alias uniqueness per parent version, so
    # surface conflicting legacy JSON explicitly.
    duplicate_alias = (
        sa.select(sa.literal(1))
        .select_from(_VERSION_SUBAGENT_BACKFILL)
        .group_by(
            _VERSION_SUBAGENT_BACKFILL.c.workspace_id,
            _VERSION_SUBAGENT_BACKFILL.c.parent_version_id,
            _VERSION_SUBAGENT_BACKFILL.c.alias,
        )
        .having(sa.func.count() > 1)
        .limit(1)
    )
    if bind.execute(duplicate_alias).first() is not None:
        raise RuntimeError("Cannot normalize preset version subagents: duplicate alias")

    # A disabled legacy config must not contain child bindings. Comparing JSONB
    # values preserves the distinction between boolean true and the string "true".
    disabled_with_children = (
        sa.select(sa.literal(1))
        .select_from(
            _VERSION_SUBAGENT_BACKFILL.join(
                _AGENT_PRESET_VERSION,
                _AGENT_PRESET_VERSION.c.id
                == _VERSION_SUBAGENT_BACKFILL.c.parent_version_id,
            )
        )
        .where(
            _AGENT_PRESET_VERSION.c.agents["enabled"].is_distinct_from(
                sa.cast(sa.literal("true"), JSONB())
            )
        )
        .limit(1)
    )
    if bind.execute(disabled_with_children).first() is not None:
        raise RuntimeError(
            "Cannot normalize preset version subagents: disabled config has children"
        )

    # Validation succeeded for the whole dataset; copy the staged mappings into
    # the durable normalized edge table.
    op.execute(
        sa.insert(_AGENT_PRESET_VERSION_SUBAGENT).from_select(
            (
                "parent_preset_version_id",
                "child_preset_id",
                "alias",
                "description",
                "max_turns",
                "workspace_id",
            ),
            sa.select(
                _VERSION_SUBAGENT_BACKFILL.c.parent_version_id,
                _VERSION_SUBAGENT_BACKFILL.c.child_id,
                _VERSION_SUBAGENT_BACKFILL.c.alias,
                _VERSION_SUBAGENT_BACKFILL.c.description,
                _VERSION_SUBAGENT_BACKFILL.c.max_turns,
                _VERSION_SUBAGENT_BACKFILL.c.workspace_id,
            ).order_by(
                _VERSION_SUBAGENT_BACKFILL.c.parent_version_id,
                _VERSION_SUBAGENT_BACKFILL.c.alias,
            ),
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
    # migration transaction has applied NOT NULL. PostgreSQL table locks do not
    # have a SQLAlchemy Core construct.
    bind.execute(sa.text("LOCK TABLE skill IN SHARE ROW EXCLUSIVE MODE"))

    live_predicate = sa.and_(
        _SKILL.c.deleted_at.is_(None),
        _SKILL.c.archived_at.is_(None),
    )
    occupied_rows = (
        bind.execute(
            sa.select(_SKILL.c.workspace_id, _SKILL.c.slug).where(
                live_predicate,
                _SKILL.c.slug.is_not(None),
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
            sa.select(_SKILL.c.id, _SKILL.c.workspace_id, _SKILL.c.name)
            .where(live_predicate, _SKILL.c.slug.is_(None))
            .order_by(
                _SKILL.c.workspace_id,
                _SKILL.c.created_at,
                _SKILL.c.id,
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
        bind.execute(_SKILL.update().where(_SKILL.c.id == row["id"]).values(slug=slug))
        occupied.add(slug)
        if counter > 1:
            _report_skill_slug_rename(dict(row), counter)

    # Deleted rows do not participate in the live unique index, but they still
    # need a value before the column can become NOT NULL.
    bind.execute(
        _SKILL.update().where(_SKILL.c.slug.is_(None)).values(slug=_SKILL.c.name)
    )

    missing_count = bind.scalar(
        sa.select(sa.func.count()).select_from(_SKILL).where(_SKILL.c.slug.is_(None))
    )
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

    bindings = tuple(
        sa.table(
            table,
            sa.column("skill_id", sa.UUID()),
            sa.column("skill_version_id", sa.UUID()),
            sa.column("workspace_id", sa.UUID()),
        )
        for table in ("agent_preset_skill", "agent_preset_version_skill")
    )

    # Fail before writing when the old pinned representation cannot be
    # reconstructed. Operators must publish the referenced heads first.
    unresolvable_binding = sa.union_all(
        *(
            sa.select(sa.literal(1))
            .select_from(
                binding.outerjoin(
                    _SKILL,
                    sa.and_(
                        _SKILL.c.workspace_id == binding.c.workspace_id,
                        _SKILL.c.id == binding.c.skill_id,
                    ),
                )
            )
            .where(
                binding.c.skill_version_id.is_(None),
                _SKILL.c.current_version_id.is_(None),
            )
            for binding in bindings
        )
    ).limit(1)
    if op.get_bind().execute(unresolvable_binding).first() is not None:
        raise RuntimeError(
            "Cannot downgrade ResourceHead skill edges: "
            "publish every referenced skill before retrying"
        )

    for binding in bindings:
        # Re-pin normalized ResourceHead bindings to each head's current version
        # before restoring the old NOT NULL constraint.
        op.execute(
            binding.update()
            .where(binding.c.skill_version_id.is_(None))
            .values(
                skill_version_id=sa.select(_SKILL.c.current_version_id)
                .where(
                    _SKILL.c.id == binding.c.skill_id,
                    _SKILL.c.workspace_id == binding.c.workspace_id,
                )
                .scalar_subquery()
            )
        )
        op.alter_column(
            binding.name,
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
