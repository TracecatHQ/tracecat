"""Tests for the event source, trigger, binding and event outbox migration."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.db.models import (
    AgentPreset,
    Base,
    EventOutbox,
    EventSource,
    ObjectStorageTrigger,
    Organization,
    Trigger,
    TriggerBinding,
    Workflow,
    Workspace,
)

MIGRATION_REVISION = "d55f7e6e72a2"
PREVIOUS_REVISION = "e573b3acd133"
TABLES = {
    "event_source",
    "trigger",
    "object_storage_trigger",
    "trigger_binding",
    "event_outbox",
}
_POSTGRESQL_ENUM_DIFFS = frozenset({"create_enum", "drop_enum", "change_enum_variants"})


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


@pytest.fixture(scope="module")
def db_url() -> Iterator[str]:
    """A fresh database migrated to this revision.

    Every test leaves it at this revision, so tests can run in any order.
    """
    admin = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    name = f"test_trigger_tables_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        url = TEST_DB_CONFIG.test_url_sync.replace(TEST_DB_CONFIG.test_db_name, name)
        _alembic(url, "upgrade", MIGRATION_REVISION)
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


def _only_trigger_tables(
    _object: object,
    name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: object,
) -> bool:
    return type_ != "table" or name in TABLES


def _model_drift(engine: Engine) -> list[Any]:
    with engine.connect() as conn:
        context = MigrationContext.configure(
            conn,
            opts={
                "include_object": _only_trigger_tables,
                "compare_type": True,
                "compare_server_default": True,
            },
        )
        # alembic_postgresql_enum, once imported by any test, adds
        # database-wide enum diffs that `include_object` cannot filter.
        return [
            diff
            for diff in compare_metadata(context, Base.metadata)
            if not (isinstance(diff, tuple) and diff[0] in _POSTGRESQL_ENUM_DIFFS)
        ]


def _tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                " AND tablename = ANY(:names)"
            ),
            {"names": list(TABLES)},
        )
        return set(rows.scalars())


def _rls_tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_policies p"
                " ON p.tablename = c.relname AND p.policyname = 'rls_policy_' || c.relname"
                " WHERE c.relrowsecurity AND c.relname = ANY(:names)"
                " AND p.cmd = 'ALL'"
                " AND p.qual LIKE '%workspace_id = %app.current_workspace_id%'"
                " AND p.with_check LIKE '%workspace_id = %app.current_workspace_id%'"
            ),
            {"names": list(TABLES)},
        )
        return set(rows.scalars())


def _workspaces(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    org, ws, other_ws = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with Session(engine) as db, db.begin():
        db.add(Organization(id=org, name="o", slug=f"o-{org.hex}"))
        db.flush()
        db.add_all(
            [
                Workspace(id=ws, organization_id=org, name="w"),
                Workspace(id=other_ws, organization_id=org, name="x"),
            ]
        )
    return ws, other_ws


def _source(ws: uuid.UUID, topic: str = "t") -> EventSource:
    return EventSource(
        id=uuid.uuid4(),
        workspace_id=ws,
        kind="aws_sns",
        name="bucket",
        config={"topic_arn": f"arn:aws:sns:us-east-1:123456789012:{topic}"},
        endpoint_token=uuid.uuid4().hex,
    )


def _trigger(ws: uuid.UUID) -> Trigger:
    return Trigger(id=uuid.uuid4(), workspace_id=ws, kind="object_storage", name="t")


def _settings(trigger: Trigger, source_id: uuid.UUID) -> ObjectStorageTrigger:
    return ObjectStorageTrigger(
        trigger_id=trigger.id, workspace_id=trigger.workspace_id, source_id=source_id
    )


def _event(ws: uuid.UUID, source_id: uuid.UUID, key: str) -> EventOutbox:
    return EventOutbox(
        kind="object_storage",
        workspace_id=ws,
        source_id=source_id,
        occurrence_key=key,
        payload={},
    )


def _violated(exc: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    # psycopg's Diagnostic is untyped here; constraint_name is always present.
    diag = getattr(exc.value.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def _refused(engine: Engine, row: Base, constraint: str) -> None:
    with pytest.raises(IntegrityError) as exc, Session(engine) as db, db.begin():
        db.add(row)
    assert _violated(exc) == constraint


def _add(engine: Engine, *rows: Base) -> None:
    """Insert rows in order, one flush each, so parents land first."""
    with Session(engine, expire_on_commit=False) as db, db.begin():
        for row in rows:
            db.add(row)
            db.flush()


def test_upgrade_matches_the_models_and_downgrades(db_url: str, engine: Engine) -> None:
    assert _model_drift(engine) == []
    assert _rls_tables(engine) == TABLES

    _alembic(db_url, "downgrade", PREVIOUS_REVISION)
    assert _tables(engine) == set()
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    assert _tables(engine) == TABLES
    assert _model_drift(engine) == []


def test_children_stay_in_their_parents_workspace(engine: Engine) -> None:
    ws, other_ws = _workspaces(engine)
    source, trigger = _source(ws), _trigger(ws)
    preset_id = uuid.uuid4()
    _add(
        engine,
        source,
        trigger,
        AgentPreset(
            id=preset_id,
            workspace_id=other_ws,
            name="p",
            slug=f"p-{preset_id.hex}",
            model_name="m",
            model_provider="p",
        ),
    )

    foreign_trigger = _trigger(other_ws)
    _add(engine, foreign_trigger)
    _refused(
        engine,
        _settings(foreign_trigger, source.id),
        "fk_object_storage_trigger_source_id_workspace_id_event_source",
    )
    _refused(
        engine,
        ObjectStorageTrigger(
            trigger_id=trigger.id, workspace_id=other_ws, source_id=uuid.uuid4()
        ),
        "fk_object_storage_trigger_trigger_id_workspace_id_trigger",
    )
    _refused(
        engine,
        TriggerBinding(
            workspace_id=other_ws,
            trigger_id=trigger.id,
            target_type="agent",
            agent_preset_id=preset_id,
            instruction="i",
        ),
        "fk_trigger_binding_trigger_id_workspace_id_trigger",
    )
    _refused(
        engine,
        TriggerBinding(
            workspace_id=ws,
            trigger_id=trigger.id,
            target_type="agent",
            agent_preset_id=preset_id,
            instruction="i",
        ),
        "fk_trigger_binding_agent_preset_id_workspace_id_agent_preset",
    )
    _refused(
        engine,
        _event(other_ws, source.id, "k" * 64),
        "fk_event_outbox_source_id_workspace_id_event_source",
    )


def test_settings_follow_their_trigger_and_hold_their_source(engine: Engine) -> None:
    ws, _ = _workspaces(engine)
    source, trigger = _source(ws), _trigger(ws)
    _add(engine, source, trigger, _settings(trigger, source.id))

    # A source with triggers is not deleted out from under them.
    with pytest.raises(IntegrityError) as exc, Session(engine) as db, db.begin():
        db.execute(delete(EventSource).where(EventSource.id == source.id))
    assert (
        _violated(exc)
        == "fk_object_storage_trigger_source_id_workspace_id_event_source"
    )

    with Session(engine) as db, db.begin():
        db.execute(delete(Trigger).where(Trigger.id == trigger.id))
    with Session(engine) as db:
        assert db.get(ObjectStorageTrigger, trigger.id) is None
        assert db.get(EventSource, source.surrogate_id) is not None


def test_deleting_a_workspace_removes_everything(engine: Engine) -> None:
    ws, _ = _workspaces(engine)
    source, trigger = _source(ws), _trigger(ws)
    _add(
        engine,
        source,
        trigger,
        _settings(trigger, source.id),
        _event(ws, source.id, "c" * 64),
    )

    with Session(engine) as db, db.begin():
        db.execute(delete(Workspace).where(Workspace.id == ws))
    with Session(engine) as db:
        assert db.get(ObjectStorageTrigger, trigger.id) is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(EventSource)
                .where(EventSource.workspace_id == ws)
            )
            == 0
        )


def test_binding_target_shape_and_uniqueness(engine: Engine) -> None:
    ws, _ = _workspaces(engine)
    trigger = _trigger(ws)
    workflow = Workflow(id=uuid.uuid4(), workspace_id=ws, title="w", description="")
    _add(
        engine,
        workflow,
        trigger,
        TriggerBinding(
            workspace_id=ws,
            trigger_id=trigger.id,
            target_type="workflow",
            workflow_id=workflow.id,
        ),
    )

    def binding(**values: Any) -> TriggerBinding:
        return TriggerBinding(workspace_id=ws, trigger_id=trigger.id, **values)

    # A workflow binding carries no instruction; an agent binding needs one.
    _refused(
        engine,
        binding(target_type="workflow", workflow_id=workflow.id, instruction="i"),
        "ck_trigger_binding_target",
    )
    _refused(
        engine,
        binding(target_type="agent", workflow_id=workflow.id),
        "ck_trigger_binding_target",
    )
    _refused(
        engine,
        binding(target_type="workflow", workflow_id=workflow.id),
        "uq_trigger_binding_trigger_id_workflow_id",
    )

    # Deleting the workflow removes its binding.
    with Session(engine) as db, db.begin():
        db.execute(delete(Workflow).where(Workflow.id == workflow.id))
    with Session(engine) as db:
        count = db.scalar(
            select(func.count())
            .select_from(TriggerBinding)
            .where(TriggerBinding.trigger_id == trigger.id)
        )
    assert count == 0


def test_outbox_rows_are_unique_per_source_and_default_ready(engine: Engine) -> None:
    ws, other_ws = _workspaces(engine)
    source, other_source = _source(ws), _source(other_ws)
    key = "a" * 64
    first, second = _event(ws, source.id, key), _event(other_ws, other_source.id, key)
    # Another workspace subscribed to the same feed gets its own row.
    _add(engine, source, other_source, first, second)
    assert second.id > first.id

    _refused(
        engine,
        _event(ws, source.id, key),
        "uq_event_outbox_source_id_occurrence_key",
    )

    with Session(engine) as db, db.begin():
        row = db.get_one(EventOutbox, first.id)
        assert row.next_attempt_at is not None
        assert row.attempts == 0
        # A stopped row is never ready again.
        row.next_attempt_at = None
    with Session(engine) as db:
        ready = db.scalars(
            select(EventOutbox.id).where(
                EventOutbox.workspace_id == ws,
                EventOutbox.next_attempt_at <= func.now(),
            )
        ).all()
    assert ready == []

    # Deleting a source drops its queued events.
    with Session(engine) as db, db.begin():
        db.execute(delete(EventSource).where(EventSource.id == other_source.id))
    with Session(engine) as db:
        assert db.get(EventOutbox, second.id) is None


def test_one_source_per_sns_topic_per_workspace(engine: Engine) -> None:
    ws, other_ws = _workspaces(engine)
    _add(engine, _source(ws, "shared"), _source(other_ws, "shared"))
    _refused(engine, _source(ws, "shared"), "uq_event_source_aws_sns_topic")
