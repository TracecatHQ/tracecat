"""Tests for normalized agent preset subagent edge backfill."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import orjson
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "44320bf05445"
PREVIOUS_REVISION = "c6a8d4f3b2e1"


def _invoke_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = db_url
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_alembic(db_url: str, *args: str) -> None:
    result = _invoke_alembic(db_url, *args)
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
            TEST_DB_CONFIG.test_db_name, db_name
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
    preset_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
    slug: str,
    agents: dict[str, object],
) -> None:
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
                retries,
                agents
            )
            VALUES (
                :id,
                :workspace_id,
                :name,
                :slug,
                'test-model',
                'test-provider',
                3,
                CAST(:agents AS jsonb)
            )
            """
        ),
        {
            "id": preset_id,
            "workspace_id": workspace_id,
            "name": name,
            "slug": slug,
            "agents": orjson.dumps(agents).decode(),
        },
    )


def test_subagent_edge_migration_backfills_head_and_version_refs(
    migration_db_url: str,
) -> None:
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    child_by_id = uuid.uuid4()
    child_by_slug = uuid.uuid4()
    parent_id = uuid.uuid4()
    parent_version_id = uuid.uuid4()
    agents: dict[str, object] = {
        "enabled": True,
        "subagents": [
            {
                "preset": "id-child-old-slug",
                "preset_id": str(child_by_id),
                "preset_version_id": str(uuid.uuid4()),
                "preset_version": 1,
                "name": "triage",
                "description": "Triage alerts",
                "max_turns": 4,
            },
            {"preset": "slug-child"},
        ],
    }

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, 'Subagent edge org', :slug, true)
                    """
                ),
                {
                    "id": organization_id,
                    "slug": f"subagent-edge-org-{organization_id.hex[:8]}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES (:id, :organization_id, 'Subagent edge workspace')
                    """
                ),
                {"id": workspace_id, "organization_id": organization_id},
            )
            _insert_preset(
                conn,
                preset_id=child_by_id,
                workspace_id=workspace_id,
                name="ID child",
                slug="id-child-current-slug",
                agents={"enabled": False},
            )
            _insert_preset(
                conn,
                preset_id=child_by_slug,
                workspace_id=workspace_id,
                name="Slug child",
                slug="slug-child",
                agents={"enabled": False},
            )
            _insert_preset(
                conn,
                preset_id=parent_id,
                workspace_id=workspace_id,
                name="Parent",
                slug="parent",
                agents=agents,
            )
            conn.execute(
                text(
                    """
                    INSERT INTO agent_preset_version (
                        id,
                        preset_id,
                        version,
                        model_name,
                        model_provider,
                        retries,
                        workspace_id,
                        agents
                    )
                    VALUES (
                        :id,
                        :preset_id,
                        1,
                        'test-model',
                        'test-provider',
                        3,
                        :workspace_id,
                        CAST(:agents AS jsonb)
                    )
                    """
                ),
                {
                    "id": parent_version_id,
                    "preset_id": parent_id,
                    "workspace_id": workspace_id,
                    "agents": orjson.dumps(agents).decode(),
                },
            )

        _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

        with engine.begin() as conn:
            head_enabled = conn.execute(
                text("SELECT agents_enabled FROM agent_preset WHERE id = :id"),
                {"id": parent_id},
            ).scalar_one()
            version_enabled = conn.execute(
                text("SELECT agents_enabled FROM agent_preset_version WHERE id = :id"),
                {"id": parent_version_id},
            ).scalar_one()
            head_rows = (
                conn.execute(
                    text(
                        """
                        SELECT child_preset_id, alias, description, max_turns, position
                        FROM agent_preset_subagent
                        WHERE parent_preset_id = :parent_id
                        ORDER BY position
                        """
                    ),
                    {"parent_id": parent_id},
                )
                .mappings()
                .all()
            )
            version_rows = (
                conn.execute(
                    text(
                        """
                        SELECT child_preset_id, alias, description, max_turns, position
                        FROM agent_preset_version_subagent
                        WHERE parent_preset_version_id = :parent_version_id
                        ORDER BY position
                        """
                    ),
                    {"parent_version_id": parent_version_id},
                )
                .mappings()
                .all()
            )

        assert head_enabled is True
        assert version_enabled is True
        assert [row["child_preset_id"] for row in head_rows] == [
            child_by_id,
            child_by_slug,
        ]
        assert [row["alias"] for row in head_rows] == ["triage", "slug-child"]
        assert head_rows[0]["description"] == "Triage alerts"
        assert head_rows[0]["max_turns"] == 4
        assert [row["position"] for row in head_rows] == [0, 1]
        assert version_rows == head_rows
    finally:
        engine.dispose()


def test_subagent_edge_migration_rejects_cross_workspace_refs(
    migration_db_url: str,
) -> None:
    organization_id = uuid.uuid4()
    parent_workspace_id = uuid.uuid4()
    child_workspace_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, 'Cross workspace org', :slug, true)
                    """
                ),
                {
                    "id": organization_id,
                    "slug": f"cross-workspace-org-{organization_id.hex[:8]}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES
                        (:parent_id, :organization_id, 'Parent workspace'),
                        (:child_id, :organization_id, 'Child workspace')
                    """
                ),
                {
                    "parent_id": parent_workspace_id,
                    "child_id": child_workspace_id,
                    "organization_id": organization_id,
                },
            )
            _insert_preset(
                conn,
                preset_id=child_id,
                workspace_id=child_workspace_id,
                name="Foreign child",
                slug="foreign-child",
                agents={"enabled": False},
            )
            _insert_preset(
                conn,
                preset_id=parent_id,
                workspace_id=parent_workspace_id,
                name="Parent",
                slug="parent",
                agents={
                    "enabled": True,
                    "subagents": [
                        {
                            "preset": "foreign-child",
                            "preset_id": str(child_id),
                            "preset_version_id": str(uuid.uuid4()),
                            "preset_version": 1,
                        }
                    ],
                },
            )

        result = _invoke_alembic(
            migration_db_url,
            "upgrade",
            MIGRATION_REVISION,
        )

        assert result.returncode != 0
        assert (
            "unresolved or cross-workspace head reference"
            in result.stdout + result.stderr
        )
    finally:
        engine.dispose()


def test_subagent_edge_tables_reject_new_cross_workspace_refs(
    migration_db_url: str,
) -> None:
    """Composite foreign keys enforce tenant ownership after the backfill."""

    organization_id = uuid.uuid4()
    parent_workspace_id = uuid.uuid4()
    child_workspace_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    parent_version_id = uuid.uuid4()
    child_id = uuid.uuid4()

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, 'Edge constraint org', :slug, true)
                    """
                ),
                {
                    "id": organization_id,
                    "slug": f"edge-constraint-org-{organization_id.hex[:8]}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES
                        (:parent_id, :organization_id, 'Parent workspace'),
                        (:child_id, :organization_id, 'Child workspace')
                    """
                ),
                {
                    "parent_id": parent_workspace_id,
                    "child_id": child_workspace_id,
                    "organization_id": organization_id,
                },
            )
            _insert_preset(
                conn,
                preset_id=parent_id,
                workspace_id=parent_workspace_id,
                name="Parent",
                slug="parent",
                agents={"enabled": False},
            )
            _insert_preset(
                conn,
                preset_id=child_id,
                workspace_id=child_workspace_id,
                name="Foreign child",
                slug="foreign-child",
                agents={"enabled": False},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO agent_preset_version (
                        id,
                        preset_id,
                        version,
                        model_name,
                        model_provider,
                        retries,
                        workspace_id,
                        agents
                    )
                    VALUES (
                        :id,
                        :preset_id,
                        1,
                        'test-model',
                        'test-provider',
                        3,
                        :workspace_id,
                        '{"enabled": false}'::jsonb
                    )
                    """
                ),
                {
                    "id": parent_version_id,
                    "preset_id": parent_id,
                    "workspace_id": parent_workspace_id,
                },
            )

        _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO agent_preset_subagent (
                            id,
                            parent_preset_id,
                            child_preset_id,
                            alias,
                            position,
                            workspace_id
                        )
                        VALUES (
                            :id,
                            :parent_id,
                            :child_id,
                            'foreign-child',
                            0,
                            :workspace_id
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "parent_id": parent_id,
                        "child_id": child_id,
                        "workspace_id": parent_workspace_id,
                    },
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO agent_preset_version_subagent (
                            id,
                            parent_preset_version_id,
                            child_preset_id,
                            alias,
                            position,
                            workspace_id
                        )
                        VALUES (
                            :id,
                            :parent_version_id,
                            :child_id,
                            'foreign-child',
                            0,
                            :workspace_id
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "parent_version_id": parent_version_id,
                        "child_id": child_id,
                        "workspace_id": parent_workspace_id,
                    },
                )
    finally:
        engine.dispose()
