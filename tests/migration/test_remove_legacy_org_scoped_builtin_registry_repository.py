"""Tests for removing legacy org-scoped builtin registry repositories."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "0a1e3100a432"
PREVIOUS_REVISION = "6171727be56a"


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
    db_name = f"test_registry_cleanup_{uuid.uuid4().hex[:8]}"
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


def _seed_legacy_registry_rows(db_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    engine = create_engine(db_url, poolclass=NullPool)
    organization_id = uuid.uuid4()
    legacy_repo_id = uuid.uuid4()
    legacy_version_id = uuid.uuid4()
    platform_repo_id = uuid.uuid4()
    platform_version_id = uuid.uuid4()

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, 'Test org', :slug, true)
                    """
                ),
                {"id": organization_id, "slug": f"test-org-{organization_id.hex[:8]}"},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO registry_repository (id, organization_id, origin, current_version_id)
                    VALUES (:id, :organization_id, 'tracecat_registry', NULL)
                    """
                ),
                {
                    "id": legacy_repo_id,
                    "organization_id": organization_id,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO registry_version (
                        id,
                        organization_id,
                        repository_id,
                        version,
                        manifest,
                        tarball_uri
                    )
                    VALUES (
                        :id,
                        :organization_id,
                        :repository_id,
                        '1.0.0',
                        CAST('{}' AS jsonb),
                        's3://test-bucket/legacy.tar.gz'
                    )
                    """
                ),
                {
                    "id": legacy_version_id,
                    "organization_id": organization_id,
                    "repository_id": legacy_repo_id,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE registry_repository
                    SET current_version_id = :version_id
                    WHERE id = :repository_id
                    """
                ),
                {
                    "version_id": legacy_version_id,
                    "repository_id": legacy_repo_id,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO platform_registry_repository (id, origin, current_version_id)
                    VALUES (:id, 'tracecat_registry', NULL)
                    """
                ),
                {"id": platform_repo_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO platform_registry_version (
                        id,
                        repository_id,
                        version,
                        manifest,
                        tarball_uri
                    )
                    VALUES (
                        :id,
                        :repository_id,
                        '1.0.0',
                        CAST('{}' AS jsonb),
                        's3://test-bucket/platform.tar.gz'
                    )
                    """
                ),
                {
                    "id": platform_version_id,
                    "repository_id": platform_repo_id,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE platform_registry_repository
                    SET current_version_id = :version_id
                    WHERE id = :repository_id
                    """
                ),
                {
                    "version_id": platform_version_id,
                    "repository_id": platform_repo_id,
                },
            )
    finally:
        engine.dispose()

    return legacy_repo_id, platform_repo_id


def test_upgrade_removes_legacy_org_scoped_builtin_registry_repository(
    migration_db_url: str,
) -> None:
    db_url = migration_db_url
    legacy_repo_id, platform_repo_id = _seed_legacy_registry_rows(db_url)
    _run_alembic(db_url, "upgrade", MIGRATION_REVISION)

    engine = create_engine(db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            legacy_repo_count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM registry_repository
                    WHERE id = :repository_id
                    """
                ),
                {"repository_id": legacy_repo_id},
            ).scalar_one()
            assert legacy_repo_count == 0

            legacy_version_count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM registry_version
                    WHERE repository_id = :repository_id
                    """
                ),
                {"repository_id": legacy_repo_id},
            ).scalar_one()
            assert legacy_version_count == 0

            platform_repo_count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM platform_registry_repository
                    WHERE id = :repository_id
                    """
                ),
                {"repository_id": platform_repo_id},
            ).scalar_one()
            assert platform_repo_count == 1
    finally:
        engine.dispose()
