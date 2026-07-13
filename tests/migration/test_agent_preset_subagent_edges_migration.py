"""Migration contract for version-owned agent preset edges."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from typing import Final

import orjson
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

PREVIOUS_REVISION: Final = "c6a8d4f3b2e1"
EXPAND_REVISION: Final = "44320bf05445"
PROVENANCE_REVISION: Final = "b4e6c8a2d0f1"
CUTOVER_REVISION: Final = "d2e4f6a8b0c1"
CONTRACT_REVISION: Final = "c7d9e1f3a5b2"


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
    db_name = f"test_preset_version_edges_{uuid.uuid4().hex[:8]}"
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


def _setup_workspace(conn: Connection, *, label: str) -> uuid.UUID:
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO organization (id, name, slug, is_active)
            VALUES (:id, :name, :slug, true)
            """
        ),
        {
            "id": organization_id,
            "name": f"{label} org",
            "slug": f"{label}-org-{organization_id.hex[:8]}",
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
            "name": f"{label} workspace",
        },
    )
    return workspace_id


def _insert_preset(
    conn: Connection,
    *,
    workspace_id: uuid.UUID,
    preset_id: uuid.UUID,
    slug: str,
    agents: dict[str, object] | None = None,
    deleted: bool = False,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO agent_preset (
                id, workspace_id, name, slug, model_name, model_provider,
                retries, agents, deleted_at
            )
            VALUES (
                :id, :workspace_id, :name, :slug, 'test-model',
                'test-provider', 3, CAST(:agents AS jsonb),
                CASE WHEN :deleted THEN now() ELSE NULL END
            )
            """
        ),
        {
            "id": preset_id,
            "workspace_id": workspace_id,
            "name": slug,
            "slug": slug,
            "agents": orjson.dumps(agents or {"enabled": False}).decode(),
            "deleted": deleted,
        },
    )


def _insert_version(
    conn: Connection,
    *,
    workspace_id: uuid.UUID,
    preset_id: uuid.UUID,
    version_id: uuid.UUID,
    version: int,
    agents: dict[str, object],
    subagents_enabled: bool | None = None,
    include_marker: bool = False,
) -> None:
    marker_column = ", subagents_enabled" if include_marker else ""
    marker_value = ", :subagents_enabled" if include_marker else ""
    conn.execute(
        text(
            f"""
            INSERT INTO agent_preset_version (
                id, preset_id, version, model_name, model_provider, retries,
                workspace_id, agents{marker_column}
            )
            VALUES (
                :id, :preset_id, :version, 'test-model', 'test-provider', 3,
                :workspace_id, CAST(:agents AS jsonb){marker_value}
            )
            """
        ),
        {
            "id": version_id,
            "preset_id": preset_id,
            "version": version,
            "workspace_id": workspace_id,
            "agents": orjson.dumps(agents).decode(),
            "subagents_enabled": subagents_enabled,
        },
    )


def _insert_version_edge(
    conn: Connection,
    *,
    workspace_id: uuid.UUID,
    version_id: uuid.UUID,
    child_id: uuid.UUID,
    alias: str,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO agent_preset_version_subagent (
                id, parent_preset_version_id, child_preset_id, alias,
                workspace_id
            )
            VALUES (:id, :version_id, :child_id, :alias, :workspace_id)
            """
        ),
        {
            "id": uuid.uuid4(),
            "version_id": version_id,
            "child_id": child_id,
            "alias": alias,
            "workspace_id": workspace_id,
        },
    )


def _column(
    conn: Connection, table_name: str, column_name: str
) -> dict[str, object] | None:
    row = (
        conn.execute(
            text(
                """
                SELECT is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = :table_name
                  AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


def _table_exists(conn: Connection, table_name: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        ).scalar_one()
    )


def test_expand_backfills_version_edges_and_preserves_legacy_schema(
    migration_db_url: str,
) -> None:
    workspace_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    parent_version_id = uuid.uuid4()
    child_by_id = uuid.uuid4()
    child_by_slug = uuid.uuid4()
    agents: dict[str, object] = {
        "enabled": True,
        "subagents": [
            {
                "preset": "old-child-slug",
                "preset_id": str(child_by_id),
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
            workspace_id = _setup_workspace(conn, label="expand")
            _insert_preset(
                conn,
                workspace_id=workspace_id,
                preset_id=child_by_id,
                slug="current-child-slug",
            )
            _insert_preset(
                conn,
                workspace_id=workspace_id,
                preset_id=child_by_slug,
                slug="slug-child",
            )
            _insert_preset(
                conn,
                workspace_id=workspace_id,
                preset_id=parent_id,
                slug="parent",
                agents=agents,
            )
            _insert_version(
                conn,
                workspace_id=workspace_id,
                preset_id=parent_id,
                version_id=parent_version_id,
                version=1,
                agents=agents,
            )

        _run_alembic(migration_db_url, "upgrade", EXPAND_REVISION)

        with engine.begin() as conn:
            rows = (
                conn.execute(
                    text(
                        """
                        SELECT child_preset_id, alias, description, max_turns
                        FROM agent_preset_version_subagent
                        WHERE parent_preset_version_id = :version_id
                        ORDER BY alias
                        """
                    ),
                    {"version_id": parent_version_id},
                )
                .mappings()
                .all()
            )
            marker, legacy_agents = conn.execute(
                text(
                    """
                    SELECT subagents_enabled, agents
                    FROM agent_preset_version WHERE id = :id
                    """
                ),
                {"id": parent_version_id},
            ).one()
            assert [row["child_preset_id"] for row in rows] == [
                child_by_slug,
                child_by_id,
            ]
            assert [row["alias"] for row in rows] == ["slug-child", "triage"]
            assert rows[1]["description"] == "Triage alerts"
            assert rows[1]["max_turns"] == 4
            assert marker is True
            assert legacy_agents == agents
            assert not _table_exists(conn, "agent_preset_subagent")
            assert _column(conn, "agent_preset", "subagents_enabled") is None
            assert _column(conn, "agent_preset", "model_name") == {
                "is_nullable": "YES",
                "column_default": None,
            }
            assert _column(conn, "agent_preset", "model_provider") == {
                "is_nullable": "YES",
                "column_default": None,
            }
            conn.execute(
                text(
                    """
                    UPDATE agent_preset
                    SET model_name = NULL, model_provider = NULL
                    WHERE id = :id
                    """
                ),
                {"id": parent_id},
            )
            assert _column(conn, "agent_preset_skill", "skill_version_id") == {
                "is_nullable": "YES",
                "column_default": None,
            }
            assert _column(conn, "agent_preset_version_skill", "skill_version_id") == {
                "is_nullable": "YES",
                "column_default": None,
            }

            late_version_id = uuid.uuid4()
            _insert_version(
                conn,
                workspace_id=workspace_id,
                preset_id=parent_id,
                version_id=late_version_id,
                version=2,
                agents={"enabled": False},
            )
            assert (
                conn.execute(
                    text(
                        "SELECT subagents_enabled FROM agent_preset_version WHERE id = :id"
                    ),
                    {"id": late_version_id},
                ).scalar_one()
                is None
            )

            foreign_workspace_id = _setup_workspace(conn, label="foreign")
            foreign_child_id = uuid.uuid4()
            _insert_preset(
                conn,
                workspace_id=foreign_workspace_id,
                preset_id=foreign_child_id,
                slug="foreign-child",
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _insert_version_edge(
                    conn,
                    workspace_id=workspace_id,
                    version_id=parent_version_id,
                    child_id=foreign_child_id,
                    alias="foreign-child",
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("agents", "expected"),
    [
        pytest.param(
            {"enabled": True, "subagents": [{"preset": "missing"}]},
            "unresolved or cross-workspace",
            id="unresolved",
        ),
        pytest.param(
            {
                "enabled": True,
                "subagents": [
                    {"preset": "child", "name": "duplicate"},
                    {"preset": "child", "name": "duplicate"},
                ],
            },
            "duplicate alias",
            id="duplicate-alias",
        ),
        pytest.param(
            {"enabled": False, "subagents": [{"preset": "child"}]},
            "disabled config has children",
            id="disabled",
        ),
        pytest.param(
            {"subagents": [{"preset": "child"}]},
            "disabled config has children",
            id="missing-enabled",
        ),
        pytest.param(
            {"enabled": "true", "subagents": [{"preset": "child"}]},
            "disabled config has children",
            id="string-enabled",
        ),
    ],
)
def test_expand_rejects_invalid_version_projection_atomically(
    migration_db_url: str,
    agents: dict[str, object],
    expected: str,
) -> None:
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            workspace_id = _setup_workspace(conn, label="invalid-expand")
            child_id = uuid.uuid4()
            parent_id = uuid.uuid4()
            _insert_preset(
                conn, workspace_id=workspace_id, preset_id=child_id, slug="child"
            )
            _insert_preset(
                conn,
                workspace_id=workspace_id,
                preset_id=parent_id,
                slug="parent",
            )
            _insert_version(
                conn,
                workspace_id=workspace_id,
                preset_id=parent_id,
                version_id=uuid.uuid4(),
                version=1,
                agents=agents,
            )

        result = _invoke_alembic(migration_db_url, "upgrade", EXPAND_REVISION)
        assert result.returncode != 0
        assert expected in result.stdout + result.stderr
        with engine.begin() as conn:
            assert (
                conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == PREVIOUS_REVISION
            )
            assert not _table_exists(conn, "agent_preset_version_subagent")
            assert _column(conn, "agent_preset_version", "subagents_enabled") is None
    finally:
        engine.dispose()


def test_expand_prefers_active_slug_and_keeps_unique_tombstone_target(
    migration_db_url: str,
) -> None:
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            workspace_id = _setup_workspace(conn, label="tombstones")
            active_id = uuid.uuid4()
            shared_tombstone_id = uuid.uuid4()
            unique_tombstone_id = uuid.uuid4()
            parent_id = uuid.uuid4()
            version_id = uuid.uuid4()
            _insert_preset(
                conn,
                workspace_id=workspace_id,
                preset_id=shared_tombstone_id,
                slug="shared",
                deleted=True,
            )
            _insert_preset(
                conn, workspace_id=workspace_id, preset_id=active_id, slug="shared"
            )
            _insert_preset(
                conn,
                workspace_id=workspace_id,
                preset_id=unique_tombstone_id,
                slug="deleted-only",
                deleted=True,
            )
            _insert_preset(
                conn, workspace_id=workspace_id, preset_id=parent_id, slug="parent"
            )
            _insert_version(
                conn,
                workspace_id=workspace_id,
                preset_id=parent_id,
                version_id=version_id,
                version=1,
                agents={
                    "enabled": True,
                    "subagents": [
                        {"preset": "shared"},
                        {"preset": "deleted-only"},
                    ],
                },
            )

        _run_alembic(migration_db_url, "upgrade", EXPAND_REVISION)
        with engine.begin() as conn:
            targets = dict(
                conn.execute(
                    text(
                        """
                        SELECT alias, child_preset_id
                        FROM agent_preset_version_subagent
                        WHERE parent_preset_version_id = :version_id
                        """
                    ),
                    {"version_id": version_id},
                )
                .tuples()
                .all()
            )
        assert targets == {
            "shared": active_id,
            "deleted-only": unique_tombstone_id,
        }
    finally:
        engine.dispose()


def test_expand_downgrade_roundtrip(migration_db_url: str) -> None:
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            workspace_id = _setup_workspace(conn, label="roundtrip")
            child_id = uuid.uuid4()
            parent_id = uuid.uuid4()
            version_id = uuid.uuid4()
            _insert_preset(
                conn, workspace_id=workspace_id, preset_id=child_id, slug="child"
            )
            _insert_preset(
                conn, workspace_id=workspace_id, preset_id=parent_id, slug="parent"
            )
            _insert_version(
                conn,
                workspace_id=workspace_id,
                preset_id=parent_id,
                version_id=version_id,
                version=1,
                agents={
                    "enabled": True,
                    "subagents": [{"preset": "child", "name": "helper"}],
                },
            )

        _run_alembic(migration_db_url, "upgrade", EXPAND_REVISION)
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE agent_preset
                    SET current_version_id = :version_id,
                        model_name = NULL,
                        model_provider = NULL
                    WHERE id = :preset_id
                    """
                ),
                {"version_id": version_id, "preset_id": parent_id},
            )
        _run_alembic(migration_db_url, "downgrade", PREVIOUS_REVISION)
        with engine.begin() as conn:
            assert not _table_exists(conn, "agent_preset_version_subagent")
            assert _column(conn, "agent_preset_version", "subagents_enabled") is None
            agents = conn.execute(
                text("SELECT agents FROM agent_preset_version WHERE id = :id"),
                {"id": version_id},
            ).scalar_one()
            assert agents["subagents"][0]["preset"] == "child"
            assert _column(conn, "agent_preset_version_skill", "skill_version_id") == {
                "is_nullable": "NO",
                "column_default": None,
            }
            assert _column(conn, "agent_preset", "model_name") == {
                "is_nullable": "NO",
                "column_default": None,
            }
            assert conn.execute(
                text(
                    "SELECT model_name, model_provider FROM agent_preset WHERE id = :id"
                ),
                {"id": parent_id},
            ).one() == ("test-model", "test-provider")

        _run_alembic(migration_db_url, "upgrade", EXPAND_REVISION)
        with engine.begin() as conn:
            assert (
                conn.execute(
                    text(
                        """
                    SELECT child_preset_id FROM agent_preset_version_subagent
                    WHERE parent_preset_version_id = :version_id
                    """
                    ),
                    {"version_id": version_id},
                ).scalar_one()
                == child_id
            )
    finally:
        engine.dispose()


def test_cutover_reconciles_only_null_epoch_rows(migration_db_url: str) -> None:
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            workspace_id = _setup_workspace(conn, label="cutover")
            child_a = uuid.uuid4()
            child_b = uuid.uuid4()
            parent_id = uuid.uuid4()
            _insert_preset(
                conn, workspace_id=workspace_id, preset_id=child_a, slug="child-a"
            )
            _insert_preset(
                conn, workspace_id=workspace_id, preset_id=child_b, slug="child-b"
            )
            _insert_preset(
                conn, workspace_id=workspace_id, preset_id=parent_id, slug="parent"
            )
        _run_alembic(migration_db_url, "upgrade", PROVENANCE_REVISION)

        late_version_id = uuid.uuid4()
        explicit_version_id = uuid.uuid4()
        with engine.begin() as conn:
            _insert_version(
                conn,
                workspace_id=workspace_id,
                preset_id=parent_id,
                version_id=late_version_id,
                version=1,
                agents={
                    "enabled": True,
                    "subagents": [{"preset": "child-a", "name": "late"}],
                },
            )
            _insert_version_edge(
                conn,
                workspace_id=workspace_id,
                version_id=late_version_id,
                child_id=child_b,
                alias="stale",
            )
            _insert_version(
                conn,
                workspace_id=workspace_id,
                preset_id=parent_id,
                version_id=explicit_version_id,
                version=2,
                agents={"enabled": False},
                subagents_enabled=True,
                include_marker=True,
            )
            _insert_version_edge(
                conn,
                workspace_id=workspace_id,
                version_id=explicit_version_id,
                child_id=child_b,
                alias="authoritative",
            )

        _run_alembic(migration_db_url, "upgrade", CUTOVER_REVISION)
        with engine.begin() as conn:
            late_edges = conn.execute(
                text(
                    """
                    SELECT child_preset_id, alias
                    FROM agent_preset_version_subagent
                    WHERE parent_preset_version_id = :version_id
                    """
                ),
                {"version_id": late_version_id},
            ).all()
            explicit_edges = conn.execute(
                text(
                    """
                    SELECT child_preset_id, alias
                    FROM agent_preset_version_subagent
                    WHERE parent_preset_version_id = :version_id
                    """
                ),
                {"version_id": explicit_version_id},
            ).all()
            markers = dict(
                conn.execute(
                    text(
                        """
                        SELECT id, subagents_enabled FROM agent_preset_version
                        WHERE id IN (:late_id, :explicit_id)
                        """
                    ),
                    {"late_id": late_version_id, "explicit_id": explicit_version_id},
                )
                .tuples()
                .all()
            )
            marker_column = _column(conn, "agent_preset_version", "subagents_enabled")
            marker_constraint = conn.execute(
                text(
                    """
                    SELECT convalidated
                    FROM pg_constraint
                    WHERE conname =
                        'ck_agent_preset_version_subagents_enabled_not_null'
                    """
                )
            ).scalar_one()
            legacy_columns = set(
                conn.execute(
                    text(
                        """
                        SELECT table_name, column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND (
                            (table_name = 'agent_preset'
                             AND column_name = 'instructions')
                            OR (table_name = 'agent_preset_version'
                                AND column_name = 'agents')
                            OR (table_name = 'agent_preset_version_skill'
                                AND column_name = 'skill_version_id')
                          )
                        """
                    )
                )
                .tuples()
                .all()
            )
            legacy_binding_table = conn.execute(
                text("SELECT to_regclass('agent_preset_skill')")
            ).scalar_one()
        assert late_edges == [(child_a, "late")]
        assert explicit_edges == [(child_b, "authoritative")]
        assert markers == {late_version_id: True, explicit_version_id: True}
        assert marker_column == {
            "is_nullable": "YES",
            "column_default": None,
        }
        assert marker_constraint is True
        assert legacy_columns == {
            ("agent_preset", "instructions"),
            ("agent_preset_version", "agents"),
            ("agent_preset_version_skill", "skill_version_id"),
        }
        assert legacy_binding_table == "agent_preset_skill"
    finally:
        engine.dispose()


def test_cutover_rejects_invalid_late_legacy_row_atomically(
    migration_db_url: str,
) -> None:
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            workspace_id = _setup_workspace(conn, label="invalid-cutover")
            child_id = uuid.uuid4()
            parent_id = uuid.uuid4()
            _insert_preset(
                conn, workspace_id=workspace_id, preset_id=child_id, slug="child"
            )
            _insert_preset(
                conn, workspace_id=workspace_id, preset_id=parent_id, slug="parent"
            )
        _run_alembic(migration_db_url, "upgrade", PROVENANCE_REVISION)

        version_id = uuid.uuid4()
        with engine.begin() as conn:
            _insert_version(
                conn,
                workspace_id=workspace_id,
                preset_id=parent_id,
                version_id=version_id,
                version=1,
                agents={"subagents": [{"preset": "child"}]},
            )
            _insert_version_edge(
                conn,
                workspace_id=workspace_id,
                version_id=version_id,
                child_id=child_id,
                alias="stale",
            )

        result = _invoke_alembic(migration_db_url, "upgrade", CUTOVER_REVISION)
        assert result.returncode != 0
        assert "disabled config has children" in result.stdout + result.stderr
        with engine.begin() as conn:
            assert (
                conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == PROVENANCE_REVISION
            )
            assert (
                conn.execute(
                    text(
                        """
                    SELECT alias FROM agent_preset_version_subagent
                    WHERE parent_preset_version_id = :version_id
                    """
                    ),
                    {"version_id": version_id},
                ).scalar_one()
                == "stale"
            )
            assert (
                conn.execute(
                    text(
                        "SELECT subagents_enabled FROM agent_preset_version WHERE id = :id"
                    ),
                    {"id": version_id},
                ).scalar_one()
                is None
            )
            assert _column(conn, "agent_preset_version", "subagents_enabled") == {
                "is_nullable": "YES",
                "column_default": None,
            }
    finally:
        engine.dispose()


def test_contract_drops_legacy_representations_and_preserves_version_edges(
    migration_db_url: str,
) -> None:
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        parent_id = uuid.uuid4()
        child_id = uuid.uuid4()
        version_id = uuid.uuid4()
        skill_id = uuid.uuid4()
        skill_version_id = uuid.uuid4()
        with engine.begin() as conn:
            workspace_id = _setup_workspace(conn, label="contract")
            _insert_preset(
                conn, workspace_id=workspace_id, preset_id=child_id, slug="child"
            )
            _insert_preset(
                conn, workspace_id=workspace_id, preset_id=parent_id, slug="parent"
            )
            _insert_version(
                conn,
                workspace_id=workspace_id,
                preset_id=parent_id,
                version_id=version_id,
                version=1,
                agents={
                    "enabled": True,
                    "subagents": [{"preset": "child"}],
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE agent_preset SET current_version_id = :version_id
                    WHERE id = :preset_id
                    """
                ),
                {"version_id": version_id, "preset_id": parent_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO skill (
                        id, workspace_id, name, slug, draft_revision,
                        archived_at, deleted_at
                    )
                    VALUES (:id, :workspace_id, 'skill', 'skill', 0, NULL, NULL)
                    """
                ),
                {"id": skill_id, "workspace_id": workspace_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO skill_version (
                        id, skill_id, version, manifest_sha256, file_count,
                        total_size_bytes, name, workspace_id
                    )
                    VALUES (
                        :id, :skill_id, 1, :digest, 0, 0, 'skill', :workspace_id
                    )
                    """
                ),
                {
                    "id": skill_version_id,
                    "skill_id": skill_id,
                    "digest": "a" * 64,
                    "workspace_id": workspace_id,
                },
            )
            conn.execute(
                text(
                    "UPDATE skill SET current_version_id = :version_id WHERE id = :id"
                ),
                {"version_id": skill_version_id, "id": skill_id},
            )
            for table, owner_column, owner_id in (
                ("agent_preset_skill", "preset_id", parent_id),
                ("agent_preset_version_skill", "preset_version_id", version_id),
            ):
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {table} (
                            id, {owner_column}, skill_id, skill_version_id,
                            workspace_id
                        )
                        VALUES (
                            :id, :owner_id, :skill_id, :skill_version_id,
                            :workspace_id
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "owner_id": owner_id,
                        "skill_id": skill_id,
                        "skill_version_id": skill_version_id,
                        "workspace_id": workspace_id,
                    },
                )

        _run_alembic(migration_db_url, "upgrade", CONTRACT_REVISION)
        with engine.begin() as conn:
            assert not _table_exists(conn, "agent_preset_skill")
            for column_name in (
                "instructions",
                "model_name",
                "model_provider",
                "catalog_id",
                "agents",
                "retries",
            ):
                assert _column(conn, "agent_preset", column_name) is None
            assert _column(conn, "agent_preset_version", "agents") is None
            assert (
                _column(conn, "agent_preset_version_skill", "skill_version_id") is None
            )
            assert _column(conn, "agent_preset_version", "subagents_enabled") == {
                "is_nullable": "NO",
                "column_default": "false",
            }
            assert (
                conn.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM pg_constraint
                        WHERE conname =
                            'ck_agent_preset_version_subagents_enabled_not_null'
                        """
                    )
                ).scalar_one()
                == 0
            )
            assert (
                conn.execute(
                    text(
                        """
                    SELECT child_preset_id FROM agent_preset_version_subagent
                    WHERE parent_preset_version_id = :version_id
                    """
                    ),
                    {"version_id": version_id},
                ).scalar_one()
                == child_id
            )
            assert (
                conn.execute(
                    text(
                        """
                    SELECT skill_id FROM agent_preset_version_skill
                    WHERE preset_version_id = :version_id
                    """
                    ),
                    {"version_id": version_id},
                ).scalar_one()
                == skill_id
            )
            head = conn.execute(
                text(
                    """
                    SELECT name, slug, current_version_id FROM agent_preset
                    WHERE id = :id
                    """
                ),
                {"id": parent_id},
            ).one()
            assert head == ("parent", "parent", version_id)
    finally:
        engine.dispose()
