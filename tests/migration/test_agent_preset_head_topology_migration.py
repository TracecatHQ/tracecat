"""Tests for normalizing agent preset topology onto resource heads."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "a8e1f7c3b2d9"
PREVIOUS_REVISION = "44d7e75b6f4c"


def _run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = db_url
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _require_alembic_success(db_url: str, *args: str) -> None:
    result = _run_alembic(db_url, *args)
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
    db_name = f"test_agent_head_topology_{uuid.uuid4().hex[:8]}"
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
        _require_alembic_success(db_url, "upgrade", PREVIOUS_REVISION)
        yield db_url
    finally:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        default_engine.dispose()


def _insert_workspace(
    conn: Connection,
    *,
    workspace_id: uuid.UUID,
    label: str,
) -> None:
    organization_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO organization (id, name, slug, is_active)
            VALUES (:id, :name, :slug, true)
            """
        ),
        {
            "id": organization_id,
            "name": f"Agent topology org {label}",
            "slug": f"agent-topology-org-{label}-{organization_id.hex[:8]}",
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
            "name": f"Agent topology workspace {label}",
        },
    )


def _insert_preset_with_current_version(
    conn: Connection,
    *,
    workspace_id: uuid.UUID,
    slug: str,
    version_agents: dict[str, Any] | None = None,
    head_agents: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    preset_id = uuid.uuid4()
    version_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO agent_preset (
                id,
                workspace_id,
                name,
                slug,
                instructions,
                model_name,
                model_provider,
                retries,
                agents
            )
            VALUES (
                :id,
                :workspace_id,
                :name,
                :slug,
                'Preset instructions',
                'gpt-5.5',
                'openai',
                3,
                CAST(:agents AS jsonb)
            )
            """
        ),
        {
            "id": preset_id,
            "workspace_id": workspace_id,
            "name": slug.replace("-", " ").title(),
            "slug": slug,
            "agents": json.dumps(head_agents or {"enabled": False}),
        },
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
                agents
            )
            VALUES (
                :id,
                :preset_id,
                :workspace_id,
                1,
                'Preset instructions',
                'gpt-5.5',
                'openai',
                3,
                CAST(:agents AS jsonb)
            )
            """
        ),
        {
            "id": version_id,
            "preset_id": preset_id,
            "workspace_id": workspace_id,
            "agents": json.dumps(version_agents or {"enabled": False}),
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
        {"preset_id": preset_id, "version_id": version_id},
    )
    return preset_id, version_id


def _insert_skill_with_versions(
    conn: Connection,
    *,
    workspace_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    skill_id = uuid.uuid4()
    old_version_id = uuid.uuid4()
    current_version_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO skill (
                id,
                workspace_id,
                name,
                slug,
                draft_revision
            )
            VALUES (:id, :workspace_id, 'Evidence skill', 'evidence-skill', 0)
            """
        ),
        {"id": skill_id, "workspace_id": workspace_id},
    )
    conn.execute(
        text(
            """
            INSERT INTO skill_version (
                id,
                skill_id,
                workspace_id,
                version,
                manifest_sha256,
                file_count,
                total_size_bytes,
                name
            )
            VALUES
                (
                    :old_version_id,
                    :skill_id,
                    :workspace_id,
                    1,
                    :old_sha,
                    1,
                    10,
                    'Evidence skill v1'
                ),
                (
                    :current_version_id,
                    :skill_id,
                    :workspace_id,
                    2,
                    :current_sha,
                    1,
                    20,
                    'Evidence skill v2'
                )
            """
        ),
        {
            "old_version_id": old_version_id,
            "current_version_id": current_version_id,
            "skill_id": skill_id,
            "workspace_id": workspace_id,
            "old_sha": "1" * 64,
            "current_sha": "2" * 64,
        },
    )
    conn.execute(
        text(
            """
            UPDATE skill
            SET current_version_id = :current_version_id
            WHERE id = :skill_id
            """
        ),
        {"skill_id": skill_id, "current_version_id": current_version_id},
    )
    return skill_id, old_version_id, current_version_id


def test_migration_backfills_head_topology_without_mutating_legacy_shadows(
    migration_db_url: str,
) -> None:
    workspace_id = uuid.uuid4()
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            _insert_workspace(conn, workspace_id=workspace_id, label="happy")
            child_id, _ = _insert_preset_with_current_version(
                conn,
                workspace_id=workspace_id,
                slug="evidence-child",
            )
            parent_version_agents = {
                "enabled": True,
                "subagents": [
                    {
                        "preset": "evidence-child",
                        "preset_id": str(child_id),
                        "name": "evidence",
                        "description": "Collect supporting evidence.",
                        "max_turns": 7,
                    }
                ],
            }
            parent_id, parent_version_id = _insert_preset_with_current_version(
                conn,
                workspace_id=workspace_id,
                slug="triage-parent",
                version_agents=parent_version_agents,
                head_agents={"enabled": False},
            )
            skill_id, old_skill_version_id, current_skill_version_id = (
                _insert_skill_with_versions(conn, workspace_id=workspace_id)
            )
            version_skill_edge_id = uuid.uuid4()
            head_skill_edge_id = uuid.uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO agent_preset_version_skill (
                        id,
                        preset_version_id,
                        skill_id,
                        skill_version_id,
                        workspace_id
                    )
                    VALUES (
                        :id,
                        :preset_version_id,
                        :skill_id,
                        :skill_version_id,
                        :workspace_id
                    )
                    """
                ),
                {
                    "id": version_skill_edge_id,
                    "preset_version_id": parent_version_id,
                    "skill_id": skill_id,
                    "skill_version_id": current_skill_version_id,
                    "workspace_id": workspace_id,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO agent_preset_skill (
                        id,
                        preset_id,
                        skill_id,
                        skill_version_id,
                        workspace_id
                    )
                    VALUES (
                        :id,
                        :preset_id,
                        :skill_id,
                        :skill_version_id,
                        :workspace_id
                    )
                    """
                ),
                {
                    "id": head_skill_edge_id,
                    "preset_id": parent_id,
                    "skill_id": skill_id,
                    "skill_version_id": old_skill_version_id,
                    "workspace_id": workspace_id,
                },
            )

        _require_alembic_success(migration_db_url, "upgrade", MIGRATION_REVISION)

        with engine.connect() as conn:
            subagent_edge = (
                conn.execute(
                    text(
                        """
                        SELECT
                            parent_preset_id,
                            child_preset_id,
                            alias,
                            description,
                            max_turns,
                            workspace_id
                        FROM agent_preset_subagent
                        """
                    )
                )
                .mappings()
                .one()
            )
            assert subagent_edge == {
                "parent_preset_id": parent_id,
                "child_preset_id": child_id,
                "alias": "evidence",
                "description": "Collect supporting evidence.",
                "max_turns": 7,
                "workspace_id": workspace_id,
            }

            head_skill_edge = (
                conn.execute(
                    text(
                        """
                        SELECT id, skill_version_id
                        FROM agent_preset_skill
                        WHERE preset_id = :preset_id
                        """
                    ),
                    {"preset_id": parent_id},
                )
                .mappings()
                .one()
            )
            assert head_skill_edge == {
                "id": head_skill_edge_id,
                "skill_version_id": current_skill_version_id,
            }

            legacy_agents = conn.execute(
                text(
                    """
                    SELECT agents
                    FROM agent_preset_version
                    WHERE id = :version_id
                    """
                ),
                {"version_id": parent_version_id},
            ).scalar_one()
            assert legacy_agents == parent_version_agents
            legacy_skill_edge = (
                conn.execute(
                    text(
                        """
                        SELECT id, skill_version_id
                        FROM agent_preset_version_skill
                        WHERE preset_version_id = :preset_version_id
                        """
                    ),
                    {"preset_version_id": parent_version_id},
                )
                .mappings()
                .one()
            )
            assert legacy_skill_edge == {
                "id": version_skill_edge_id,
                "skill_version_id": current_skill_version_id,
            }
            assert (
                conn.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM information_schema.columns
                        WHERE table_name = 'agent_preset'
                          AND column_name = 'subagents_enabled'
                        """
                    )
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("invalid_refs", "expected_error"),
    [
        pytest.param(
            [{"preset": "missing-child", "name": "evidence"}],
            "unresolved or cross-workspace subagent head",
            id="unresolved-child",
        ),
        pytest.param(
            [
                {"preset": "first-child", "name": "duplicate"},
                {"preset": "second-child", "name": "duplicate"},
            ],
            "duplicate subagent alias",
            id="duplicate-alias",
        ),
    ],
)
def test_migration_rejects_invalid_subagent_edges_atomically(
    migration_db_url: str,
    invalid_refs: list[dict[str, Any]],
    expected_error: str,
) -> None:
    workspace_id = uuid.uuid4()
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            _insert_workspace(conn, workspace_id=workspace_id, label="invalid")
            for slug in ("first-child", "second-child"):
                _insert_preset_with_current_version(
                    conn,
                    workspace_id=workspace_id,
                    slug=slug,
                )
            _insert_preset_with_current_version(
                conn,
                workspace_id=workspace_id,
                slug="parent",
                version_agents={"enabled": True, "subagents": invalid_refs},
            )

        result = _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

        assert result.returncode != 0
        assert expected_error in result.stderr
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == PREVIOUS_REVISION
            )
            assert (
                conn.execute(
                    text("SELECT to_regclass('public.agent_preset_subagent')")
                ).scalar_one()
                is None
            )
    finally:
        engine.dispose()


def test_migration_rejects_cross_workspace_subagent_edge_atomically(
    migration_db_url: str,
) -> None:
    parent_workspace_id = uuid.uuid4()
    child_workspace_id = uuid.uuid4()
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            _insert_workspace(
                conn,
                workspace_id=parent_workspace_id,
                label="parent",
            )
            _insert_workspace(
                conn,
                workspace_id=child_workspace_id,
                label="child",
            )
            child_id, _ = _insert_preset_with_current_version(
                conn,
                workspace_id=child_workspace_id,
                slug="foreign-child",
            )
            _insert_preset_with_current_version(
                conn,
                workspace_id=parent_workspace_id,
                slug="parent",
                version_agents={
                    "enabled": True,
                    "subagents": [
                        {
                            "preset": "foreign-child",
                            "preset_id": str(child_id),
                            "name": "foreign",
                        }
                    ],
                },
            )

        result = _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

        assert result.returncode != 0
        assert "unresolved or cross-workspace subagent head" in result.stderr
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == PREVIOUS_REVISION
            )
            assert (
                conn.execute(
                    text("SELECT to_regclass('public.agent_preset_subagent')")
                ).scalar_one()
                is None
            )
    finally:
        engine.dispose()


def test_migration_rejects_mismatched_current_preset_version_atomically(
    migration_db_url: str,
) -> None:
    workspace_id = uuid.uuid4()
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            _insert_workspace(conn, workspace_id=workspace_id, label="version")
            parent_id, _ = _insert_preset_with_current_version(
                conn,
                workspace_id=workspace_id,
                slug="parent",
            )
            _, unrelated_version_id = _insert_preset_with_current_version(
                conn,
                workspace_id=workspace_id,
                slug="unrelated",
            )
            conn.execute(
                text(
                    """
                    UPDATE agent_preset
                    SET current_version_id = :unrelated_version_id
                    WHERE id = :parent_id
                    """
                ),
                {
                    "parent_id": parent_id,
                    "unrelated_version_id": unrelated_version_id,
                },
            )

        result = _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

        assert result.returncode != 0
        assert "invalid current preset version" in result.stderr
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == PREVIOUS_REVISION
            )
            assert (
                conn.execute(
                    text("SELECT to_regclass('public.agent_preset_subagent')")
                ).scalar_one()
                is None
            )
    finally:
        engine.dispose()
