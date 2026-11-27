"""Tests for the migration that links webhooks to ``webhook`` triggers."""

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

MIGRATION_REVISION = "fa8c621ea06a"
PREVIOUS_REVISION = "61ebd010e726"
_MIGRATION_FILE = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "fa8c621ea06a_link_webhooks_to_triggers.py"
)


def _migration() -> Any:
    spec = importlib.util.spec_from_file_location("webhook_triggers", _MIGRATION_FILE)
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
    name = f"test_webhook_triggers_{uuid.uuid4().hex[:8]}"
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


def _workflow(engine: Engine, ws: uuid.UUID, title: str, webhook: str) -> uuid.UUID:
    """A workflow with its webhook in ``webhook`` status, as the previous
    release writes them. Returns the workflow ID."""
    workflow_id = uuid.uuid4()
    _run(
        engine,
        "INSERT INTO workflow (id, workspace_id, title, description, status)"
        " VALUES (:id, :ws, :title, '', 'offline')",
        id=workflow_id,
        ws=ws,
        title=title,
    )
    _run(
        engine,
        "INSERT INTO webhook (id, workspace_id, workflow_id, status, methods,"
        " allowlisted_cidrs, include_headers)"
        " VALUES (:id, :ws, :wf, :status, '[\"POST\"]', '[]', false)",
        id=uuid.uuid4(),
        ws=ws,
        wf=workflow_id,
        status=webhook,
    )
    return workflow_id


def _linked(engine: Engine) -> dict[uuid.UUID, tuple[Any, ...]]:
    """Per workflow: its webhook's trigger row and that row's bindings."""
    rows = _run(
        engine,
        """
        SELECT h.workflow_id, t.kind, t.name, t.status,
            t.workspace_id = h.workspace_id, t.created_at = h.created_at,
            array_agg(ARRAY[b.target_type, b.workflow_id::text, b.status])
                FILTER (WHERE b.id IS NOT NULL)
        FROM webhook h
        LEFT JOIN "trigger" t ON t.id = h.trigger_id
        LEFT JOIN trigger_binding b ON b.trigger_id = t.id
        GROUP BY h.surrogate_id, t.surrogate_id
        """,
    )
    return {row[0]: tuple(row[1:]) for row in rows}


def _backfill(engine: Engine) -> None:
    """Rerun the backfill as a later release would: one transaction,
    workflows locked first."""
    with engine.begin() as conn:
        _migration().backfill(conn)


def _count(engine: Engine, table: str) -> int:
    return _run(engine, f'SELECT count(*) FROM "{table}"')[0][0]


def test_backfill_links_online_webhooks_only(db_url: str, engine: Engine) -> None:
    ws = _workspace(engine)
    report = _workflow(engine, ws, "Alert intake", "online")
    untitled = _workflow(engine, ws, "  ", "online")
    unused = _workflow(engine, ws, "Never used", "offline")

    _alembic(db_url, "upgrade", MIGRATION_REVISION)

    expected = {
        report: (
            "webhook",
            "Alert intake webhook",
            "online",
            True,
            True,
            [["workflow", str(report), "online"]],
        ),
        untitled: (
            "webhook",
            "Webhook",
            "online",
            True,
            True,
            [["workflow", str(untitled), "online"]],
        ),
        unused: (None, None, None, None, None, None),
    }
    assert _linked(engine) == expected

    # A second run changes nothing.
    _backfill(engine)
    assert _linked(engine) == expected
    assert (_count(engine, "trigger"), _count(engine, "trigger_binding")) == (2, 2)

    # The previous release keeps turning webhooks on during a rollout; a
    # rerun picks up just those.
    _run(
        engine,
        "UPDATE webhook SET status = 'online' WHERE workflow_id = :wf",
        wf=unused,
    )
    assert _linked(engine)[unused][0] is None
    _backfill(engine)
    assert _linked(engine)[unused][:3] == ("webhook", "Never used webhook", "online")
    assert (_count(engine, "trigger"), _count(engine, "trigger_binding")) == (3, 3)


@pytest.mark.parametrize(
    "held_row",
    [
        # A workflow delete holds the webhook before the workflow.
        "SELECT id FROM webhook WHERE workflow_id = :wf FOR UPDATE",
        # An import holds the workflow before the webhook.
        "SELECT id FROM workflow WHERE id = :wf FOR UPDATE",
    ],
)
def test_backfill_skips_rows_others_hold_and_never_waits(
    db_url: str, engine: Engine, held_row: str
) -> None:
    """Thermos r5-r7, codex: writers lock a webhook and its workflow in
    either order, so the backfill never waits for either lock: it skips the
    pair (no lock timeout below), so it cannot deadlock, and a later pass
    links it."""
    ws = _workspace(engine)
    held = _workflow(engine, ws, "Held", "online")
    free = _workflow(engine, ws, "Free", "online")
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    _run(engine, 'DELETE FROM "trigger"')
    assert _count(engine, "trigger") == 0

    with engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(text(held_row), {"wf": held})
        with engine.begin() as conn:
            # A wait would hit this timeout; skipping finishes at once.
            conn.execute(text("SET LOCAL lock_timeout = '300ms'"))
            _migration().backfill(conn)
        transaction.rollback()

    linked = _linked(engine)
    assert linked[held][0] is None
    assert linked[free][:3] == ("webhook", "Free webhook", "online")
    _backfill(engine)
    assert _linked(engine)[held][:3] == ("webhook", "Held webhook", "online")


def test_the_trigger_row_follows_its_webhook(db_url: str, engine: Engine) -> None:
    ws = _workspace(engine)
    workflow = _workflow(engine, ws, "Report", "online")
    other = _workflow(engine, ws, "Other", "online")
    _alembic(db_url, "upgrade", MIGRATION_REVISION)

    # A status the previous release writes reaches the trigger row, and the
    # webhook keeps its trigger row while offline.
    _run(
        engine,
        "UPDATE webhook SET status = 'offline' WHERE workflow_id = :wf",
        wf=workflow,
    )
    assert _linked(engine)[workflow][:3] == ("webhook", "Report webhook", "offline")

    # Deleting the trigger row leaves the webhook, unlinked.
    _run(
        engine,
        'DELETE FROM "trigger" WHERE id = (SELECT trigger_id FROM webhook'
        " WHERE workflow_id = :wf)",
        wf=other,
    )
    assert _linked(engine)[other] == (None, None, None, None, None, None)
    assert _count(engine, "webhook") == 2

    # A workflow delete cascades to its webhook and so to its trigger row.
    _run(engine, "DELETE FROM workflow WHERE id = :id", id=workflow)
    assert (_count(engine, "trigger"), _count(engine, "trigger_binding")) == (0, 0)
    assert _count(engine, "webhook") == 1


def test_one_trigger_per_webhook_and_same_workspace(
    db_url: str, engine: Engine
) -> None:
    ws = _workspace(engine)
    other_ws = _workspace(engine)
    first = _workflow(engine, ws, "First", "online")
    second = _workflow(engine, ws, "Second", "offline")
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    [(first_trigger,)] = _run(
        engine, "SELECT trigger_id FROM webhook WHERE workflow_id = :wf", wf=first
    )

    with pytest.raises(Exception, match="uq_webhook_trigger_id"):
        _run(
            engine,
            "UPDATE webhook SET trigger_id = :t WHERE workflow_id = :wf",
            t=first_trigger,
            wf=second,
        )
    foreign = uuid.uuid4()
    _run(
        engine,
        'INSERT INTO "trigger" (id, workspace_id, kind, name, status)'
        " VALUES (:id, :ws, 'webhook', 'x', 'online')",
        id=foreign,
        ws=other_ws,
    )
    with pytest.raises(Exception, match="fk_webhook_trigger_id_workspace_id_trigger"):
        _run(
            engine,
            "UPDATE webhook SET trigger_id = :t WHERE workflow_id = :wf",
            t=foreign,
            wf=second,
        )


def test_downgrade_removes_webhook_triggers_and_upgrade_again(
    db_url: str, engine: Engine
) -> None:
    ws = _workspace(engine)
    workflow = _workflow(engine, ws, "Report", "online")
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    assert _count(engine, "trigger") == 1

    _alembic(db_url, "downgrade", PREVIOUS_REVISION)

    assert (_count(engine, "trigger"), _count(engine, "trigger_binding")) == (0, 0)
    columns = _run(
        engine,
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name = 'webhook' AND column_name = 'trigger_id'",
    )
    assert columns == []
    assert _run(engine, "SELECT workflow_id, status FROM webhook") == [
        (workflow, "online")
    ]

    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    assert _linked(engine)[workflow][:3] == ("webhook", "Report webhook", "online")
