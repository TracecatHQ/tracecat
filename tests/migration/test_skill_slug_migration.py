"""Tests for the skill slug expansion migration."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "c6a8d4f3b2e1"
PREVIOUS_REVISION = "8b4f6c2d1a9e"


def _run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = db_url
    result = subprocess.run(
        ["uv", "run", "alembic", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Alembic command failed:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def _insert_organization_and_workspace(
    conn: Connection,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    label: str,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO organization (id, name, slug, is_active)
            VALUES (:id, :name, :slug, true)
            """
        ),
        {
            "id": organization_id,
            "name": f"Skill slug org {label}",
            "slug": f"skill-slug-org-{label}-{organization_id.hex[:8]}",
        },
    )
    conn.execute(
        text(
            """
            INSERT INTO workspace (id, organization_id, name)
            VALUES (:id, :organization_id, :name)
            """
        ),
        {
            "id": workspace_id,
            "organization_id": organization_id,
            "name": f"Skill slug workspace {label}",
        },
    )


def _insert_live_skills(
    conn: Connection,
    *,
    workspace_id: uuid.UUID,
    skills: Sequence[tuple[uuid.UUID, str, datetime]],
) -> None:
    for skill_id, name, created_at in skills:
        conn.execute(
            text(
                """
                INSERT INTO skill (
                    id,
                    workspace_id,
                    name,
                    draft_revision,
                    archived_at,
                    deleted_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :workspace_id,
                    :name,
                    0,
                    NULL,
                    NULL,
                    :created_at,
                    :created_at
                )
                """
            ),
            {
                "id": skill_id,
                "workspace_id": workspace_id,
                "name": name,
                "created_at": created_at,
            },
        )


@pytest.fixture(scope="function")
def migration_db_url() -> Iterator[str]:
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    db_name = f"test_skill_slug_{uuid.uuid4().hex[:8]}"
    termination_query = text(
        f"""
        SELECT pg_terminate_backend(pg_stat_activity.pid)
        FROM pg_stat_activity
        WHERE pg_stat_activity.datname = '{db_name}'
          AND pid <> pg_backend_pid();
        """
    )

    try:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))

        db_url = TEST_DB_CONFIG.test_url_sync.replace(
            TEST_DB_CONFIG.test_db_name, db_name
        )
        _run_alembic(db_url, "upgrade", PREVIOUS_REVISION)
        yield db_url
    finally:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        default_engine.dispose()


def test_skill_slug_migration_suffixes_live_duplicates_only(
    migration_db_url: str,
) -> None:
    """Live duplicate names are suffixed deterministically while tombstones keep slug."""
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    keep_id = uuid.uuid4()
    rename_id = uuid.uuid4()
    deleted_id = uuid.uuid4()
    original_slug = "collision-skill"
    deleted_at = datetime(2026, 1, 3, tzinfo=UTC)

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, 'Skill slug org', :slug, true)
                    """
                ),
                {
                    "id": organization_id,
                    "slug": f"skill-slug-org-{organization_id.hex[:8]}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES (:id, :organization_id, 'Skill slug workspace')
                    """
                ),
                {"id": workspace_id, "organization_id": organization_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO skill (
                        id,
                        workspace_id,
                        name,
                        draft_revision,
                        archived_at,
                        deleted_at,
                        created_at,
                        updated_at
                    )
                    VALUES
                        (
                            :keep_id,
                            :workspace_id,
                            :name,
                            0,
                            NULL,
                            NULL,
                            '2026-01-01 00:00:00+00',
                            '2026-01-01 00:00:00+00'
                        ),
                        (
                            :rename_id,
                            :workspace_id,
                            :name,
                            0,
                            NULL,
                            NULL,
                            '2026-01-02 00:00:00+00',
                            '2026-01-02 00:00:00+00'
                        ),
                        (
                            :deleted_id,
                            :workspace_id,
                            :name,
                            0,
                            :deleted_at,
                            :deleted_at,
                            '2026-01-03 00:00:00+00',
                            '2026-01-03 00:00:00+00'
                        )
                    """
                ),
                {
                    "keep_id": keep_id,
                    "rename_id": rename_id,
                    "deleted_id": deleted_id,
                    "workspace_id": workspace_id,
                    "name": original_slug,
                    "deleted_at": deleted_at,
                },
            )

        result = _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)
        output = f"{result.stdout}\n{result.stderr}"

        with engine.begin() as conn:
            rows = {
                row["id"]: row
                for row in (
                    conn.execute(
                        text(
                            """
                            SELECT id, slug, deleted_at
                            FROM skill
                            WHERE workspace_id = :workspace_id
                            """
                        ),
                        {"workspace_id": workspace_id},
                    )
                    .mappings()
                    .all()
                )
            }
            active_duplicate_count = conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM skill
                    WHERE workspace_id = :workspace_id
                      AND slug = :slug
                      AND deleted_at IS NULL
                    """
                ),
                {"workspace_id": workspace_id, "slug": original_slug},
            ).scalar_one()

        assert rows[keep_id]["slug"] == original_slug
        assert rows[rename_id]["slug"] == "collision-skill-2"
        assert rows[deleted_id]["slug"] == original_slug
        assert rows[deleted_id]["deleted_at"] is not None
        assert active_duplicate_count == 1
        assert f"old_slug={original_slug!r} new_slug='collision-skill-2'" in output
    finally:
        engine.dispose()


def test_skill_slug_migration_skips_existing_suffixed_slug(
    migration_db_url: str,
) -> None:
    """Renamed duplicate rows use a suffix free across all live workspace slugs."""
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    keep_foo_id = uuid.uuid4()
    rename_foo_id = uuid.uuid4()
    keep_foo_2_id = uuid.uuid4()

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            _insert_organization_and_workspace(
                conn,
                organization_id=organization_id,
                workspace_id=workspace_id,
                label="occupied-suffix",
            )
            _insert_live_skills(
                conn,
                workspace_id=workspace_id,
                skills=(
                    (keep_foo_id, "foo", datetime(2026, 1, 1, tzinfo=UTC)),
                    (rename_foo_id, "foo", datetime(2026, 1, 2, tzinfo=UTC)),
                    (keep_foo_2_id, "foo-2", datetime(2026, 1, 3, tzinfo=UTC)),
                ),
            )

        result = _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)
        output = f"{result.stdout}\n{result.stderr}"

        with engine.begin() as conn:
            rows = {
                row["id"]: row["slug"]
                for row in (
                    conn.execute(
                        text(
                            """
                            SELECT id, slug
                            FROM skill
                            WHERE workspace_id = :workspace_id
                            """
                        ),
                        {"workspace_id": workspace_id},
                    )
                    .mappings()
                    .all()
                )
            }

        assert rows == {
            keep_foo_id: "foo",
            rename_foo_id: "foo-3",
            keep_foo_2_id: "foo-2",
        }
        assert "old_slug='foo' new_slug='foo-3'" in output
    finally:
        engine.dispose()


def test_skill_slug_migration_suffixes_max_length_duplicate(
    migration_db_url: str,
) -> None:
    """Max-length duplicate slugs are truncated before the deterministic suffix."""
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    keep_id = uuid.uuid4()
    rename_id = uuid.uuid4()
    max_length_name = "a" * 64
    suffixed_name = f"{max_length_name[:62]}-2"

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            _insert_organization_and_workspace(
                conn,
                organization_id=organization_id,
                workspace_id=workspace_id,
                label="max-length",
            )
            _insert_live_skills(
                conn,
                workspace_id=workspace_id,
                skills=(
                    (keep_id, max_length_name, datetime(2026, 1, 1, tzinfo=UTC)),
                    (rename_id, max_length_name, datetime(2026, 1, 2, tzinfo=UTC)),
                ),
            )

        result = _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)
        output = f"{result.stdout}\n{result.stderr}"

        with engine.begin() as conn:
            rows = {
                row["id"]: row["slug"]
                for row in (
                    conn.execute(
                        text(
                            """
                            SELECT id, slug
                            FROM skill
                            WHERE workspace_id = :workspace_id
                            """
                        ),
                        {"workspace_id": workspace_id},
                    )
                    .mappings()
                    .all()
                )
            }

        assert rows == {keep_id: max_length_name, rename_id: suffixed_name}
        assert len(rows[rename_id]) == 64
        assert f"old_slug={max_length_name!r} new_slug={suffixed_name!r}" in output
    finally:
        engine.dispose()
