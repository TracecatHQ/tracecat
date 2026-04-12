"""Tests for workspace skills migrations with existing agent presets."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "0c6bb8f8e1d1"
PREVIOUS_REVISION = "b742858f7d69"


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
    db_name = f"test_workspace_skills_{uuid.uuid4().hex[:8]}"
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


def test_workspace_skills_migration_preserves_existing_agent_presets(
    migration_db_url: str,
) -> None:
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    preset_id = uuid.uuid4()
    preset_version_id = uuid.uuid4()

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, 'Preset org', :slug, true)
                    """
                ),
                {
                    "id": organization_id,
                    "slug": f"preset-org-{organization_id.hex[:8]}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES (:id, :organization_id, 'Preset workspace')
                    """
                ),
                {"id": workspace_id, "organization_id": organization_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO agent_preset (
                        id,
                        workspace_id,
                        name,
                        slug,
                        description,
                        instructions,
                        model_name,
                        model_provider,
                        retries,
                        enable_internet_access
                    )
                    VALUES (
                        :id,
                        :workspace_id,
                        'Existing preset',
                        'existing-preset',
                        'Preset created before skills',
                        'Use the existing preset',
                        'gpt-4o-mini',
                        'openai',
                        3,
                        false
                    )
                    """
                ),
                {"id": preset_id, "workspace_id": workspace_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO agent_preset_version (
                        id,
                        preset_id,
                        workspace_id,
                        version,
                        instructions,
                        model_name,
                        model_provider,
                        retries,
                        enable_internet_access
                    )
                    VALUES (
                        :id,
                        :preset_id,
                        :workspace_id,
                        1,
                        'Use the existing preset',
                        'gpt-4o-mini',
                        'openai',
                        3,
                        false
                    )
                    """
                ),
                {
                    "id": preset_version_id,
                    "preset_id": preset_id,
                    "workspace_id": workspace_id,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE agent_preset
                    SET current_version_id = :current_version_id
                    WHERE id = :preset_id
                    """
                ),
                {
                    "current_version_id": preset_version_id,
                    "preset_id": preset_id,
                },
            )

        _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

        with engine.begin() as conn:
            preset_row = (
                conn.execute(
                    text(
                        """
                    SELECT
                        id,
                        workspace_id,
                        slug,
                        current_version_id,
                        instructions,
                        model_name,
                        model_provider,
                        retries,
                        enable_internet_access
                    FROM agent_preset
                    WHERE id = :preset_id
                    """
                    ),
                    {"preset_id": preset_id},
                )
                .mappings()
                .one()
            )
            version_row = (
                conn.execute(
                    text(
                        """
                    SELECT
                        id,
                        preset_id,
                        workspace_id,
                        version,
                        instructions,
                        model_name,
                        model_provider,
                        retries,
                        enable_internet_access
                    FROM agent_preset_version
                    WHERE id = :preset_version_id
                    """
                    ),
                    {"preset_version_id": preset_version_id},
                )
                .mappings()
                .one()
            )
            head_skill_bindings = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_preset_skill
                    WHERE preset_id = :preset_id
                    """
                ),
                {"preset_id": preset_id},
            ).scalar_one()
            version_skill_bindings = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM agent_preset_version_skill
                    WHERE preset_version_id = :preset_version_id
                    """
                ),
                {"preset_version_id": preset_version_id},
            ).scalar_one()

            assert preset_row["id"] == preset_id
            assert preset_row["workspace_id"] == workspace_id
            assert preset_row["slug"] == "existing-preset"
            assert preset_row["current_version_id"] == preset_version_id
            assert preset_row["instructions"] == "Use the existing preset"
            assert preset_row["model_name"] == "gpt-4o-mini"
            assert preset_row["model_provider"] == "openai"
            assert preset_row["retries"] == 3
            assert preset_row["enable_internet_access"] is False

            assert version_row["id"] == preset_version_id
            assert version_row["preset_id"] == preset_id
            assert version_row["workspace_id"] == workspace_id
            assert version_row["version"] == 1
            assert version_row["instructions"] == "Use the existing preset"
            assert version_row["model_name"] == "gpt-4o-mini"
            assert version_row["model_provider"] == "openai"
            assert version_row["retries"] == 3
            assert version_row["enable_internet_access"] is False

            assert head_skill_bindings == 0
            assert version_skill_bindings == 0
    finally:
        engine.dispose()
