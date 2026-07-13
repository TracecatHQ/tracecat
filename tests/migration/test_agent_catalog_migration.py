"""Revision-pinned contract tests for the LLM catalog backfill."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

PREVIOUS_REVISION: Final = "7d23a45113ee"
MIGRATION_REVISION: Final = "96470fdcc686"
MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "96470fdcc686_v2_backfill_catalog_and_access.py"
)


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
    db_name = f"test_agent_catalog_{uuid.uuid4().hex[:8]}"
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


@pytest.fixture
def migration() -> Any:
    module_name = "_v2_backfill_migration_under_test"
    if cached := sys.modules.get(module_name):
        return cached
    spec = importlib.util.spec_from_file_location(module_name, MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_platform_catalog_loader_fails_fast_for_invalid_sources(
    migration: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(migration, "PLATFORM_CATALOG_PATH", missing)
    with pytest.raises(RuntimeError, match="missing or unreadable"):
        migration._load_platform_catalog_entries()

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    monkeypatch.setattr(migration, "PLATFORM_CATALOG_PATH", invalid)
    with pytest.raises(RuntimeError, match="invalid JSON"):
        migration._load_platform_catalog_entries()

    empty = tmp_path / "empty.json"
    empty.write_text('{"models":[]}', encoding="utf-8")
    monkeypatch.setattr(migration, "PLATFORM_CATALOG_PATH", empty)
    with pytest.raises(RuntimeError, match="non-empty models list"):
        migration._load_platform_catalog_entries()


def test_org_backfill_failure_is_not_swallowed(
    migration: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_org = uuid.uuid4()
    second_org = uuid.uuid4()
    visited: list[uuid.UUID] = []
    monkeypatch.setattr(
        migration,
        "_orgs_with_any_provider_secret",
        lambda _conn: [first_org, second_org],
    )

    def _fail_org_backfill(_conn: object, *, org_id: uuid.UUID) -> None:
        visited.append(org_id)
        raise RuntimeError("boom")

    monkeypatch.setattr(migration, "_backfill_org_provider_data", _fail_org_backfill)
    monkeypatch.setattr(migration, "_seed_platform_catalog_rows", lambda _conn: None)
    monkeypatch.setattr(migration.op, "get_bind", lambda: object())

    with pytest.raises(RuntimeError, match="boom"):
        migration.upgrade()
    assert visited == [first_org]


def test_catalog_backfill_preserves_sources_and_links_historical_presets(
    migration_db_url: str,
    migration: Any,
) -> None:
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    preset_id = uuid.uuid4()
    preset_version_id = uuid.uuid4()
    platform_entry = next(
        entry
        for entry in migration._load_platform_catalog_entries()
        if entry["model_provider"] == "openai"
    )
    model_name = platform_entry["model_name"]
    ciphertexts = {
        "agent-openai-credentials": b"openai-source-ciphertext",
        "agent-bedrock-credentials": b"bedrock-source-ciphertext",
        "agent-custom-model-provider-credentials": b"custom-source-ciphertext",
    }

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, 'Catalog migration org', :slug, true)
                    """
                ),
                {
                    "id": organization_id,
                    "slug": f"catalog-org-{organization_id.hex[:8]}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES (:id, :organization_id, 'Catalog migration workspace')
                    """
                ),
                {"id": workspace_id, "organization_id": organization_id},
            )
            for name, ciphertext in ciphertexts.items():
                conn.execute(
                    text(
                        """
                        INSERT INTO organization_secret (
                            id, organization_id, type, name, encrypted_keys,
                            environment
                        )
                        VALUES (
                            :id, :organization_id, 'custom', :name,
                            :encrypted_keys, 'default'
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "organization_id": organization_id,
                        "name": name,
                        "encrypted_keys": ciphertext,
                    },
                )
            conn.execute(
                text(
                    """
                    INSERT INTO agent_preset (
                        id, workspace_id, name, slug, instructions,
                        model_name, model_provider, retries
                    )
                    VALUES (
                        :id, :workspace_id, 'Historical preset',
                        'historical-preset', 'Historical instructions',
                        :model_name, 'openai', 3
                    )
                    """
                ),
                {
                    "id": preset_id,
                    "workspace_id": workspace_id,
                    "model_name": model_name,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO agent_preset_version (
                        id, preset_id, workspace_id, version, instructions,
                        model_name, model_provider, retries
                    )
                    VALUES (
                        :id, :preset_id, :workspace_id, 1,
                        'Historical instructions', :model_name, 'openai', 3
                    )
                    """
                ),
                {
                    "id": preset_version_id,
                    "preset_id": preset_id,
                    "workspace_id": workspace_id,
                    "model_name": model_name,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE agent_preset
                    SET current_version_id = :version_id
                    WHERE id = :preset_id
                    """
                ),
                {"version_id": preset_version_id, "preset_id": preset_id},
            )

        _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

        with engine.begin() as conn:
            platform_catalog = (
                conn.execute(
                    text(
                        """
                        SELECT id
                        FROM agent_catalog
                        WHERE organization_id IS NULL
                          AND custom_provider_id IS NULL
                          AND model_provider = 'openai'
                          AND model_name = :model_name
                        """
                    ),
                    {"model_name": model_name},
                )
                .mappings()
                .one()
            )
            linked = (
                conn.execute(
                    text(
                        """
                        SELECT
                            p.catalog_id AS preset_catalog_id,
                            v.catalog_id AS version_catalog_id
                        FROM agent_preset AS p
                        JOIN agent_preset_version AS v ON v.id = :version_id
                        WHERE p.id = :preset_id
                        """
                    ),
                    {"preset_id": preset_id, "version_id": preset_version_id},
                )
                .mappings()
                .one()
            )
            assert linked["preset_catalog_id"] == platform_catalog["id"]
            assert linked["version_catalog_id"] == platform_catalog["id"]

            provider_rows = {
                row["model_provider"]: row
                for row in conn.execute(
                    text(
                        """
                        SELECT id, model_provider, custom_provider_id,
                               encrypted_config
                        FROM agent_catalog
                        WHERE organization_id = :organization_id
                        """
                    ),
                    {"organization_id": organization_id},
                )
                .mappings()
                .all()
            }
            assert (
                bytes(provider_rows["bedrock"]["encrypted_config"])
                == ciphertexts["agent-bedrock-credentials"]
            )
            custom_catalog = provider_rows["custom-model-provider"]
            custom_provider_ciphertext = conn.execute(
                text(
                    """
                    SELECT encrypted_config
                    FROM agent_custom_provider
                    WHERE id = :provider_id
                    """
                ),
                {"provider_id": custom_catalog["custom_provider_id"]},
            ).scalar_one()
            assert (
                bytes(custom_catalog["encrypted_config"])
                == ciphertexts["agent-custom-model-provider-credentials"]
            )
            assert (
                bytes(custom_provider_ciphertext)
                == ciphertexts["agent-custom-model-provider-credentials"]
            )

            grants = set(
                conn.execute(
                    text(
                        """
                        SELECT catalog_id
                        FROM agent_model_access
                        WHERE organization_id = :organization_id
                          AND workspace_id IS NULL
                        """
                    ),
                    {"organization_id": organization_id},
                ).scalars()
            )
            assert {
                platform_catalog["id"],
                provider_rows["bedrock"]["id"],
                custom_catalog["id"],
            } <= grants

            source_rows = dict(
                conn.execute(
                    text(
                        """
                        SELECT name, encrypted_keys
                        FROM organization_secret
                        WHERE organization_id = :organization_id
                        """
                    ),
                    {"organization_id": organization_id},
                )
                .tuples()
                .all()
            )
            assert {name: bytes(value) for name, value in source_rows.items()} == (
                ciphertexts
            )
    finally:
        engine.dispose()
