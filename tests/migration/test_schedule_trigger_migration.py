"""Tests for the migration that links schedules to ``schedule`` triggers."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "61ebd010e726"
PREVIOUS_REVISION = "a7c3e9d1f2b4"
_MIGRATION_FILE = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "61ebd010e726_link_schedules_to_triggers.py"
)


def _migration() -> Any:
    spec = importlib.util.spec_from_file_location("schedule_triggers", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for migration tests."""
    yield


def _alembic(db_url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "TRACECAT__DB_URI": db_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    )


@pytest.fixture
def db_url() -> Iterator[str]:
    """A fresh database at the revision before this one."""
    admin = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    name = f"test_schedule_triggers_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        url = TEST_DB_CONFIG.test_url_sync.replace(TEST_DB_CONFIG.test_db_name, name)
        _alembic(url, "upgrade", PREVIOUS_REVISION)
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    engine = create_engine(db_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


def _run(engine: Engine, sql: str, **params: Any) -> list[Any]:
    with engine.begin() as conn:
        result = conn.execute(text(sql), params)
        return list(result) if result.returns_rows else []


def _workspace(engine: Engine) -> uuid.UUID:
    org, ws = uuid.uuid4(), uuid.uuid4()
    _run(
        engine,
        "INSERT INTO organization (id, name, slug, is_active)"
        " VALUES (:id, 'o', :slug, true)",
        id=org,
        slug=f"o-{org.hex}",
    )
    _run(
        engine,
        "INSERT INTO workspace (id, name, organization_id) VALUES (:id, 'w', :org)",
        id=ws,
        org=org,
    )
    return ws


def _workflow(engine: Engine, ws: uuid.UUID, title: str) -> uuid.UUID:
    workflow_id = uuid.uuid4()
    _run(
        engine,
        "INSERT INTO workflow (id, workspace_id, title, description, status)"
        " VALUES (:id, :ws, :title, '', 'offline')",
        id=workflow_id,
        ws=ws,
        title=title,
    )
    return workflow_id


def _schedule(
    engine: Engine, ws: uuid.UUID, workflow_id: uuid.UUID, status: str
) -> uuid.UUID:
    """A schedule row as the previous release writes it."""
    schedule_id = uuid.uuid4()
    _run(
        engine,
        "INSERT INTO schedule (id, workspace_id, workflow_id, status, every, inputs)"
        " VALUES (:id, :ws, :wf, :status, interval '1 hour', '{}'::jsonb)",
        id=schedule_id,
        ws=ws,
        wf=workflow_id,
        status=status,
    )
    return schedule_id


def _linked(engine: Engine) -> dict[uuid.UUID, tuple[Any, ...]]:
    """Per schedule: its trigger row and its bindings."""
    rows = _run(
        engine,
        """
        SELECT s.id, t.kind, t.name, t.status, t.workspace_id = s.workspace_id,
            t.created_at = s.created_at,
            array_agg(ARRAY[b.target_type, b.workflow_id::text, b.status])
                FILTER (WHERE b.id IS NOT NULL)
        FROM schedule s
        LEFT JOIN "trigger" t ON t.id = s.trigger_id
        LEFT JOIN trigger_binding b ON b.trigger_id = t.id
        GROUP BY s.surrogate_id, t.surrogate_id
        """,
    )
    return {row[0]: tuple(row[1:]) for row in rows}


def _backfill(engine: Engine) -> None:
    """Rerun the backfill as the contract release will: one transaction,
    workflows locked first."""
    migration = _migration()
    with engine.begin() as conn:
        conn.execute(text(migration.LOCK_WORKFLOWS_SQL))
        conn.execute(text(migration.BACKFILL_SQL))


def _count(engine: Engine, table: str) -> int:
    return _run(engine, f'SELECT count(*) FROM "{table}"')[0][0]


def test_backfill_links_every_schedule_once(db_url: str, engine: Engine) -> None:
    ws = _workspace(engine)
    report = _workflow(engine, ws, "Nightly report")
    untitled = _workflow(engine, ws, "  ")
    online = _schedule(engine, ws, report, "online")
    offline = _schedule(engine, ws, report, "offline")
    bare = _schedule(engine, ws, untitled, "online")

    _alembic(db_url, "upgrade", MIGRATION_REVISION)

    binding_to = {
        report: [["workflow", str(report), "online"]],
        untitled: [["workflow", str(untitled), "online"]],
    }
    expected = {
        online: (
            "schedule",
            "Nightly report schedule",
            "online",
            True,
            True,
            binding_to[report],
        ),
        offline: (
            "schedule",
            "Nightly report schedule",
            "offline",
            True,
            True,
            binding_to[report],
        ),
        bare: ("schedule", "Schedule", "online", True, True, binding_to[untitled]),
    }
    assert _linked(engine) == expected

    # A second run changes nothing.
    _backfill(engine)
    assert _linked(engine) == expected
    assert (_count(engine, "trigger"), _count(engine, "trigger_binding")) == (3, 3)

    # The previous release keeps writing schedules during a rollout; a rerun
    # picks up just those.
    late = _schedule(engine, ws, report, "online")
    assert _linked(engine)[late][0] is None
    _backfill(engine)
    assert _linked(engine)[late][:3] == (
        "schedule",
        "Nightly report schedule",
        "online",
    )
    assert (_count(engine, "trigger"), _count(engine, "trigger_binding")) == (4, 4)


def test_the_trigger_row_follows_its_schedule(db_url: str, engine: Engine) -> None:
    ws = _workspace(engine)
    workflow = _workflow(engine, ws, "Report")
    first = _schedule(engine, ws, workflow, "online")
    second = _schedule(engine, ws, workflow, "online")
    _alembic(db_url, "upgrade", MIGRATION_REVISION)

    # A status the previous release writes reaches the trigger row.
    _run(engine, "UPDATE schedule SET status = 'offline' WHERE id = :id", id=first)
    assert _linked(engine)[first][2] == "offline"

    # Deleting a schedule deletes its trigger row and binding.
    _run(engine, "DELETE FROM schedule WHERE id = :id", id=first)
    assert set(_linked(engine)) == {second}
    assert (_count(engine, "trigger"), _count(engine, "trigger_binding")) == (1, 1)

    # Deleting the trigger row deletes the schedule.
    _run(
        engine,
        'DELETE FROM "trigger" WHERE id = (SELECT trigger_id FROM schedule'
        " WHERE id = :id)",
        id=second,
    )
    assert _count(engine, "schedule") == 0

    # A workflow delete cascades to its schedules and so to their triggers.
    _schedule(engine, ws, workflow, "online")
    _backfill(engine)
    assert _count(engine, "trigger") == 1
    _run(engine, "DELETE FROM workflow WHERE id = :id", id=workflow)
    assert (_count(engine, "trigger"), _count(engine, "trigger_binding")) == (0, 0)


def test_one_trigger_per_schedule_and_same_workspace(
    db_url: str, engine: Engine
) -> None:
    ws = _workspace(engine)
    other_ws = _workspace(engine)
    workflow = _workflow(engine, ws, "Report")
    first = _schedule(engine, ws, workflow, "online")
    second = _schedule(engine, ws, workflow, "online")
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    [(first_trigger,)] = _run(
        engine, "SELECT trigger_id FROM schedule WHERE id = :id", id=first
    )

    with pytest.raises(Exception, match="uq_schedule_trigger_id"):
        _run(
            engine,
            "UPDATE schedule SET trigger_id = :t WHERE id = :id",
            t=first_trigger,
            id=second,
        )
    foreign = uuid.uuid4()
    _run(
        engine,
        'INSERT INTO "trigger" (id, workspace_id, kind, name, status)'
        " VALUES (:id, :ws, 'schedule', 'x', 'online')",
        id=foreign,
        ws=other_ws,
    )
    with pytest.raises(Exception, match="fk_schedule_trigger_id_workspace_id_trigger"):
        _run(
            engine,
            "UPDATE schedule SET trigger_id = :t WHERE id = :id",
            t=foreign,
            id=second,
        )


def test_downgrade_removes_schedule_triggers_and_upgrade_again(
    db_url: str, engine: Engine
) -> None:
    ws = _workspace(engine)
    workflow = _workflow(engine, ws, "Report")
    schedule = _schedule(engine, ws, workflow, "online")
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    assert _count(engine, "trigger") == 1

    _alembic(db_url, "downgrade", PREVIOUS_REVISION)

    assert (_count(engine, "trigger"), _count(engine, "trigger_binding")) == (0, 0)
    columns = _run(
        engine,
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name = 'schedule' AND column_name = 'trigger_id'",
    )
    assert columns == []
    assert _run(engine, "SELECT id FROM schedule") == [(schedule,)]

    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    assert _linked(engine)[schedule][:3] == ("schedule", "Report schedule", "online")
