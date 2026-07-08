"""add skill slug

Revision ID: c6a8d4f3b2e1
Revises: 8b4f6c2d1a9e
Create Date: 2026-07-08 00:00:00.000000

Expand-phase note: the slug column deliberately stays NULLABLE here. During a
rolling deploy, old app pods still insert skills without a slug; enforcing NOT
NULL in this revision would break those writers mid-rollout. The contract
release (which removes the legacy write path) re-runs the backfill and applies
SET NOT NULL once no slug-less writers remain.

"""

import logging
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6a8d4f3b2e1"
down_revision: str | None = "8b4f6c2d1a9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")
SKILL_SLUG_MAX_LENGTH = 64


def _report_skill_slug_rename(row: dict[str, Any]) -> None:
    # Identifiers only: slugs derive from customer-authored names and must not
    # land in logs. Operators can look up the row by id.
    message = (
        "Renamed live skill slug collision during migration: "
        f"workspace_id={row['workspace_id']} "
        f"skill_id={row['skill_id']} "
        f"suffix_counter={row['suffix_counter']}"
    )
    print(message)
    logger.info(message)


def _suffixed_skill_slug(slug: str, counter: int) -> str:
    suffix = f"-{counter}"
    return f"{slug[: SKILL_SLUG_MAX_LENGTH - len(suffix)]}{suffix}"


def _deduplicate_live_skill_slugs(bind: Connection) -> None:
    # Liveness must match the expand window's effective-dead semantics: legacy
    # pods archive by setting ONLY archived_at (deleted_at stays NULL), and the
    # service read path treats either column as dead. A deleted_at-only
    # predicate would let a legacy-archived row claim the canonical slug and
    # permanently suffix the truly-live row.
    live_rows = (
        bind.execute(
            sa.text(
                """
                SELECT id, workspace_id, slug AS old_slug
                FROM skill
                WHERE deleted_at IS NULL AND archived_at IS NULL
                ORDER BY workspace_id, slug, created_at, id
                """
            )
        )
        .mappings()
        .all()
    )
    used_slugs_by_workspace: dict[Any, set[str]] = {}
    seen_counts_by_workspace_slug: dict[tuple[Any, str], int] = {}
    for row in live_rows:
        used_slugs_by_workspace.setdefault(row["workspace_id"], set()).add(
            row["old_slug"]
        )

    for row in live_rows:
        workspace_id = row["workspace_id"]
        old_slug = row["old_slug"]
        seen_key = (workspace_id, old_slug)
        seen_count = seen_counts_by_workspace_slug.get(seen_key, 0)
        seen_counts_by_workspace_slug[seen_key] = seen_count + 1
        if seen_count == 0:
            continue

        used_slugs = used_slugs_by_workspace[workspace_id]
        counter = 2
        while (new_slug := _suffixed_skill_slug(old_slug, counter)) in used_slugs:
            counter += 1
        bind.execute(
            sa.text("UPDATE skill SET slug = :new_slug WHERE id = :id"),
            {"id": row["id"], "new_slug": new_slug},
        )
        used_slugs.add(new_slug)
        _report_skill_slug_rename(
            {
                "workspace_id": workspace_id,
                "skill_id": row["id"],
                "suffix_counter": counter,
            }
        )


def _raise_if_live_skill_slug_duplicates_remain(bind: Connection) -> None:
    duplicates = (
        bind.execute(
            sa.text(
                """
                SELECT workspace_id, min(id::text) AS sample_skill_id,
                       count(*) AS row_count
                FROM skill
                WHERE deleted_at IS NULL AND archived_at IS NULL
                GROUP BY workspace_id, slug
                HAVING count(*) > 1
                ORDER BY workspace_id
                LIMIT 20
                """
            )
        )
        .mappings()
        .all()
    )
    if duplicates:
        # Identifiers only (no slug values): slugs derive from
        # customer-authored names and must not land in logs.
        details = ", ".join(
            f"workspace_id={row['workspace_id']} "
            f"sample_skill_id={row['sample_skill_id']} "
            f"count={row['row_count']}"
            for row in duplicates
        )
        raise RuntimeError(
            "Skill slug migration left duplicate live rows before index creation: "
            f"{details}"
        )


def upgrade() -> None:
    op.add_column("skill", sa.Column("slug", sa.String(length=64), nullable=True))
    op.execute(
        """
        UPDATE skill
        SET slug = name
        WHERE slug IS NULL
        """
    )

    bind = op.get_bind()
    _deduplicate_live_skill_slugs(bind)
    _raise_if_live_skill_slug_duplicates_remain(bind)

    # Column intentionally left nullable during the expand window; the
    # contract migration re-backfills and applies SET NOT NULL.
    op.create_index(op.f("ix_skill_slug"), "skill", ["slug"], unique=False)
    # The predicate matches the expand window's effective-dead semantics
    # (deleted OR archived = dead): legacy pods archive by setting only
    # archived_at, and such rows must not occupy their slug for the whole
    # rolling window. The contract migration re-backfills
    # deleted_at = archived_at WHERE deleted_at IS NULL AND archived_at IS NOT
    # NULL, then recreates this index on deleted_at only.
    op.create_index(
        "uq_skill_workspace_slug_active",
        "skill",
        ["workspace_id", "slug"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_skill_workspace_slug_active", table_name="skill")
    op.drop_index(op.f("ix_skill_slug"), table_name="skill")
    op.drop_column("skill", "slug")
