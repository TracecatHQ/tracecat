"""Tests for normalized agent preset subagent edges."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "7b4d9a2c1e6f"
PREVIOUS_REVISION = "44d7e75b6f4c"


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


@pytest.fixture
def migration_db_url() -> Iterator[str]:
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    db_name = f"test_preset_subagent_edges_{uuid.uuid4().hex[:8]}"
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


def _insert_preset(
    conn: Connection,
    *,
    workspace_id: uuid.UUID,
    name: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    preset_id = uuid.uuid4()
    version_id = uuid.uuid4()
    slug = name.lower().replace(" ", "-")
    conn.execute(
        text(
            """
            INSERT INTO agent_preset (
                id,
                workspace_id,
                name,
                slug,
                model_name,
                model_provider,
                retries
            )
            VALUES (
                :id,
                :workspace_id,
                :name,
                :slug,
                'test-model',
                'test-provider',
                3
            )
            """
        ),
        {
            "id": preset_id,
            "workspace_id": workspace_id,
            "name": name,
            "slug": slug,
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
                model_name,
                model_provider,
                retries
            )
            VALUES (
                :id,
                :preset_id,
                :workspace_id,
                1,
                'test-model',
                'test-provider',
                3
            )
            """
        ),
        {
            "id": version_id,
            "preset_id": preset_id,
            "workspace_id": workspace_id,
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


def test_subagent_edge_schema_constraints_and_downgrade(
    migration_db_url: str,
) -> None:
    _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)
    engine = create_engine(migration_db_url, poolclass=NullPool)
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    other_workspace_id = uuid.uuid4()

    try:
        inspector = inspect(engine)
        assert "agents" not in {
            column["name"] for column in inspector.get_columns("agent_preset")
        }
        assert "agents" not in {
            column["name"] for column in inspector.get_columns("agent_preset_version")
        }
        assert {
            "agent_preset_subagent",
            "agent_preset_version_subagent",
        } <= set(inspector.get_table_names())

        with engine.begin() as conn:
            rls_tables = dict(
                conn.execute(
                    text(
                        """
                        SELECT relname, relrowsecurity
                        FROM pg_class
                        WHERE relname IN (
                            'agent_preset_subagent',
                            'agent_preset_version_subagent'
                        )
                        """
                    )
                )
                .tuples()
                .all()
            )
            assert rls_tables == {
                "agent_preset_subagent": True,
                "agent_preset_version_subagent": True,
            }
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, 'Test org', :slug, true)
                    """
                ),
                {
                    "id": organization_id,
                    "slug": f"test-org-{organization_id.hex[:8]}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES
                        (:workspace_id, :organization_id, 'Test workspace'),
                        (:other_workspace_id, :organization_id, 'Other workspace')
                    """
                ),
                {
                    "workspace_id": workspace_id,
                    "other_workspace_id": other_workspace_id,
                    "organization_id": organization_id,
                },
            )
            parent_id, parent_version_id = _insert_preset(
                conn,
                workspace_id=workspace_id,
                name="Parent",
            )
            child_id, child_version_id = _insert_preset(
                conn,
                workspace_id=workspace_id,
                name="Child",
            )
            other_child_id, _ = _insert_preset(
                conn,
                workspace_id=other_workspace_id,
                name="Other Child",
            )
            conn.execute(
                text(
                    """
                    INSERT INTO agent_preset_subagent (
                        id,
                        workspace_id,
                        parent_preset_id,
                        child_preset_id,
                        alias,
                        description,
                        max_turns
                    )
                    VALUES (
                        :id,
                        :workspace_id,
                        :parent_id,
                        :child_id,
                        'stable-child',
                        'Handle specialized tasks',
                        4
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "workspace_id": workspace_id,
                    "parent_id": parent_id,
                    "child_id": child_id,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO agent_preset_version_subagent (
                        id,
                        workspace_id,
                        parent_preset_version_id,
                        child_preset_id,
                        alias,
                        description,
                        max_turns
                    )
                    VALUES (
                        :id,
                        :workspace_id,
                        :parent_version_id,
                        :child_id,
                        'stable-child',
                        'Handle specialized tasks',
                        4
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "workspace_id": workspace_id,
                    "parent_version_id": parent_version_id,
                    "child_id": child_id,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE agent_preset
                    SET deleted_at = now()
                    WHERE id = :child_id
                    """
                ),
                {"child_id": child_id},
            )
            assert (
                conn.execute(
                    text(
                        """
                    SELECT count(*)
                    FROM agent_preset_subagent
                    WHERE child_preset_id = :child_id
                    """
                    ),
                    {"child_id": child_id},
                ).scalar_one()
                == 1
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM agent_preset WHERE id = :child_id"),
                    {"child_id": child_id},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO agent_preset_subagent (
                            id,
                            workspace_id,
                            parent_preset_id,
                            child_preset_id,
                            alias
                        )
                        VALUES (
                            :id,
                            :workspace_id,
                            :parent_id,
                            :child_id,
                            'cross-workspace'
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "workspace_id": workspace_id,
                        "parent_id": parent_id,
                        "child_id": other_child_id,
                    },
                )

        _run_alembic(migration_db_url, "downgrade", PREVIOUS_REVISION)
        inspector = inspect(engine)
        assert {
            "agent_preset_subagent",
            "agent_preset_version_subagent",
        }.isdisjoint(inspector.get_table_names())
        agent_columns = {
            column["name"]: column for column in inspector.get_columns("agent_preset")
        }
        assert "agents" in agent_columns
        assert "subagents" in str(agent_columns["agents"]["default"])
        with engine.connect() as conn:
            head_agents = conn.execute(
                text("SELECT agents FROM agent_preset WHERE id = :preset_id"),
                {"preset_id": parent_id},
            ).scalar_one()
            version_agents = conn.execute(
                text("SELECT agents FROM agent_preset_version WHERE id = :version_id"),
                {"version_id": parent_version_id},
            ).scalar_one()
        for agents in (head_agents, version_agents):
            assert agents == {
                "subagents": [
                    {
                        "preset": "child",
                        "preset_id": str(child_id),
                        "preset_version_id": str(child_version_id),
                        "preset_version": 1,
                        "name": "stable-child",
                        "description": "Handle specialized tasks",
                        "max_turns": 4,
                    }
                ]
            }
    finally:
        engine.dispose()
