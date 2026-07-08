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
    message = (
        "Renamed live skill slug collision during migration: "
        f"workspace_id={row['workspace_id']} "
        f"old_slug={row['old_slug']!r} "
        f"new_slug={row['new_slug']!r}"
    )
    print(message)
    logger.info(message)


def _suffixed_skill_slug(slug: str, counter: int) -> str:
    suffix = f"-{counter}"
    return f"{slug[: SKILL_SLUG_MAX_LENGTH - len(suffix)]}{suffix}"


def _deduplicate_live_skill_slugs(bind: Connection) -> None:
    live_rows = (
        bind.execute(
            sa.text(
                """
                SELECT id, workspace_id, slug AS old_slug
                FROM skill
                WHERE deleted_at IS NULL
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
                "old_slug": old_slug,
                "new_slug": new_slug,
            }
        )


def _raise_if_live_skill_slug_duplicates_remain(bind: Connection) -> None:
    duplicates = (
        bind.execute(
            sa.text(
                """
                SELECT workspace_id, slug, count(*) AS row_count
                FROM skill
                WHERE deleted_at IS NULL
                GROUP BY workspace_id, slug
                HAVING count(*) > 1
                ORDER BY workspace_id, slug
                LIMIT 20
                """
            )
        )
        .mappings()
        .all()
    )
    if duplicates:
        details = ", ".join(
            f"workspace_id={row['workspace_id']} "
            f"slug={row['slug']!r} "
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
    op.create_index(
        "uq_skill_workspace_slug_active",
        "skill",
        ["workspace_id", "slug"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_skill_workspace_slug_active", table_name="skill")
    op.drop_index(op.f("ix_skill_slug"), table_name="skill")
    op.drop_column("skill", "slug")
