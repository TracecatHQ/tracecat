"""Tests for the case text versioning migration."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "598b32358ec5"
PREVIOUS_REVISION = "35fa9fc71258"


def _run_alembic(db_url: str, *args: str) -> None:
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


@pytest.fixture(scope="function")
def migration_db_url() -> Iterator[str]:
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    db_name = f"test_case_versions_{uuid.uuid4().hex[:8]}"
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
            TEST_DB_CONFIG.test_db_name,
            db_name,
        )
        _run_alembic(db_url, "upgrade", PREVIOUS_REVISION)
        yield db_url
    finally:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        default_engine.dispose()


def test_case_versions_migration_creates_baselines_and_rls(
    migration_db_url: str,
) -> None:
    engine = create_engine(migration_db_url, poolclass=NullPool)
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()
    baseline_timestamp = datetime(2025, 1, 2, 3, 4, tzinfo=UTC)

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, 'Test org', :slug, true)
                    """
                ),
                {
                    "id": organization_id,
                    "slug": f"case-version-org-{organization_id.hex[:8]}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES (:id, :organization_id, 'Test workspace')
                    """
                ),
                {"id": workspace_id, "organization_id": organization_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO "case" (
                        id,
                        case_number,
                        workspace_id,
                        summary,
                        description,
                        priority,
                        severity,
                        status,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        :case_id,
                        1,
                        :workspace_id,
                        'Existing summary',
                        '<p>Existing description</p>',
                        'MEDIUM',
                        'LOW',
                        'NEW',
                        :baseline_timestamp,
                        :baseline_timestamp
                    )
                    """
                ),
                {
                    "case_id": case_id,
                    "workspace_id": workspace_id,
                    "baseline_timestamp": baseline_timestamp,
                },
            )

        _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

        with engine.connect() as conn:
            versions = conn.execute(
                text(
                    """
                    SELECT case_id, field::text, version, content, user_id
                    FROM case_version
                    ORDER BY case_id, field, version
                    """
                )
            ).all()
            assert set(versions) == {
                (case_id, "SUMMARY", 1, "Existing summary", None),
                (
                    case_id,
                    "DESCRIPTION",
                    1,
                    "<p>Existing description</p>",
                    None,
                ),
            }
            version_timestamps = conn.execute(
                text(
                    """
                    SELECT DISTINCT created_at, updated_at
                    FROM case_version
                    """
                )
            ).one()
            assert tuple(version_timestamps) == (
                baseline_timestamp,
                baseline_timestamp,
            )
            assert conn.execute(
                text(
                    """
                    SELECT relrowsecurity AND EXISTS (
                        SELECT 1
                        FROM pg_policies
                        WHERE tablename = 'case_version'
                          AND policyname = 'rls_policy_case_version'
                    )
                    FROM pg_class WHERE oid = 'case_version'::regclass
                    """
                )
            ).scalar_one()

        _run_alembic(migration_db_url, "downgrade", PREVIOUS_REVISION)

        with engine.connect() as conn:
            assert conn.execute(
                text(
                    """
                    SELECT to_regclass('public.case_version') IS NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM pg_type WHERE typname = 'caseversionfield'
                    )
                    """
                )
            ).scalar_one()
    finally:
        engine.dispose()
