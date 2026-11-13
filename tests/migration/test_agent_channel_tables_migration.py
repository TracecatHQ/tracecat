"""Tests for the agent channel bot and thread link tables migration."""

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
from sqlalchemy import Engine, create_engine, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.agent.channel_bots.schemas import (
    ChannelBotProvider,
    ChannelBotStatus,
    ChannelBotTransport,
)
from tracecat.db.models import (
    AgentChannelBot,
    AgentChannelThread,
    AgentPreset,
    AgentSession,
    Base,
    Organization,
    Workspace,
)

MIGRATION_REVISION = "e20249352688"
PREVIOUS_REVISION = "ec392d96f2b0"
TABLES = {"agent_channel_bot", "agent_channel_thread"}
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
    """A fresh database migrated to the revision before this one."""
    admin = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    name = f"test_channel_tables_{uuid.uuid4().hex[:8]}"
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


def _workspace(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    """An organization with two workspaces; returns the workspace ids."""
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


def _preset(db: Session, ws: uuid.UUID) -> uuid.UUID:
    preset_id = uuid.uuid4()
    db.add(
        AgentPreset(
            id=preset_id,
            workspace_id=ws,
            name="p",
            slug=f"p-{preset_id.hex}",
            model_name="m",
            model_provider="p",
        )
    )
    db.flush()
    return preset_id


def _links(engine: Engine, ws: uuid.UUID) -> set[tuple[uuid.UUID, uuid.UUID, str]]:
    with Session(engine) as db:
        rows = db.execute(
            select(
                AgentChannelThread.session_id,
                AgentChannelThread.bot_id,
                AgentChannelThread.thread_key,
            ).where(AgentChannelThread.workspace_id == ws)
        )
        return set(rows.tuples())


def _only_channel_tables(
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
                "include_object": _only_channel_tables,
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


def _rls_tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_policies p"
                " ON p.tablename = c.relname AND p.policyname = 'rls_policy_' || c.relname"
                " WHERE c.relrowsecurity AND c.relname LIKE 'agent_channel_%'"
                " AND c.relname <> 'agent_channel_token'"
                # Both predicates scope to the workspace, not just a policy by that name.
                " AND p.cmd = 'ALL'"
                " AND p.qual LIKE '%workspace_id = %app.current_workspace_id%'"
                " AND p.with_check LIKE '%workspace_id = %app.current_workspace_id%'"
            )
        )
        return set(rows.scalars())


def _channel_tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                " AND tablename IN ('agent_channel_bot', 'agent_channel_thread')"
            )
        )
        return set(rows.scalars())


def test_upgrade_matches_the_models_and_downgrades(db_url: str, engine: Engine) -> None:
    _alembic(db_url, "downgrade", PREVIOUS_REVISION)
    assert _model_drift(engine)
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    assert _model_drift(engine) == []
    assert _rls_tables(engine) == TABLES

    _alembic(db_url, "downgrade", PREVIOUS_REVISION)
    assert _channel_tables(engine) == set()
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    assert _channel_tables(engine) == TABLES


def _bot(ws: uuid.UUID, preset_id: uuid.UUID, **values: object) -> AgentChannelBot:
    bot_id = uuid.uuid4()
    defaults: dict[str, object] = {
        "id": bot_id,
        "workspace_id": ws,
        "agent_preset_id": preset_id,
        "provider": "slack",
        "provider_tenant_id": "T1",
        "provider_app_id": f"A{bot_id.hex}",
        "manifest_version": 1,
        "encrypted_credentials": b"sealed",
    }
    return AgentChannelBot(**(defaults | values))


def _session(ws: uuid.UUID, preset_id: uuid.UUID) -> AgentSession:
    return AgentSession(
        id=uuid.uuid4(),
        workspace_id=ws,
        title="t",
        entity_type="agent_preset",
        entity_id=preset_id,
    )


def _link(
    bot_id: uuid.UUID,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    thread_ts: str = "1.1",
) -> AgentChannelThread:
    thread = {
        "provider": "slack",
        "team_id": "T1",
        "channel_id": "C1",
        "thread_ts": thread_ts,
    }
    return AgentChannelThread(
        session_id=session_id,
        workspace_id=workspace_id,
        bot_id=bot_id,
        thread=thread,
        thread_key=f"slack:T1:C1:{thread_ts}",
    )


def _violated(exc: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    diag = getattr(exc.value.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def test_links_stay_in_their_bots_and_sessions_workspace(
    db_url: str, engine: Engine
) -> None:
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    ws, other_ws = _workspace(engine)
    with Session(engine, expire_on_commit=False) as db, db.begin():
        preset_id = _preset(db, ws)
        bot = _bot(ws, preset_id)
        mine, also_mine = _session(ws, preset_id), _session(ws, preset_id)
        foreign = _session(other_ws, preset_id)
        db.add_all([bot, mine, also_mine, foreign])

    def link(
        session_id: uuid.UUID, workspace_id: uuid.UUID = ws, thread_ts: str = "1.1"
    ) -> AgentChannelThread:
        return _link(bot.id, session_id, workspace_id, thread_ts)

    with Session(engine) as db, db.begin():
        db.add(link(mine.id))
    rejected = [
        # One session per thread per bot.
        (link(also_mine.id), "uq_agent_channel_thread_bot_id_thread_key"),
        # A link names its bot's workspace, so RLS on the link means the bot's tenant.
        (
            link(foreign.id, other_ws, "1.2"),
            "fk_agent_channel_thread_bot_id_workspace_id_agent_channel_bot",
        ),
        # A link never names another workspace's session.
        (
            link(foreign.id, ws, "1.3"),
            "fk_agent_channel_thread_session_id_workspace_id_agent_session",
        ),
    ]
    for row, constraint in rejected:
        with (
            pytest.raises(IntegrityError) as exc,
            Session(engine) as db,
            db.begin(),
        ):
            db.add(row)
        assert _violated(exc) == constraint

    with Session(engine) as db, db.begin():
        db.execute(delete(AgentSession).where(AgentSession.id == mine.id))
    assert _links(engine, ws) == set()


def test_a_link_can_be_inserted_before_its_session(db_url: str, engine: Engine) -> None:
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    ws, _ = _workspace(engine)
    with Session(engine, expire_on_commit=False) as db, db.begin():
        preset_id = _preset(db, ws)
        bot, session = _bot(ws, preset_id), _session(ws, preset_id)
        db.add(bot)
        db.flush()
        db.add(_link(bot.id, session.id, ws))
        # An immediate session key would refuse the link here.
        db.flush()
        db.add(session)
    assert _links(engine, ws) == {(session.id, bot.id, "slack:T1:C1:1.1")}


def test_bot_checks_match_the_api_enums(db_url: str, engine: Engine) -> None:
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    ws, _ = _workspace(engine)
    with Session(engine) as db, db.begin():
        preset_id = _preset(db, ws)
        db.add_all(
            _bot(
                ws,
                preset_id,
                provider=provider.value,
                status=status.value,
                transport=transport.value,
            )
            for provider in ChannelBotProvider
            for status in ChannelBotStatus
            for transport in ChannelBotTransport
        )
    for bogus, constraint in (
        ({"provider": "discord"}, "ck_agent_channel_bot_provider"),
        ({"status": "deleted"}, "ck_agent_channel_bot_status"),
        ({"transport": "webhook"}, "ck_agent_channel_bot_transport"),
    ):
        with (
            pytest.raises(IntegrityError) as exc,
            Session(engine) as db,
            db.begin(),
        ):
            db.add(_bot(ws, preset_id, **bogus))
        assert _violated(exc) == constraint


def test_one_bot_per_installed_app(db_url: str, engine: Engine) -> None:
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    ws, _ = _workspace(engine)
    app = {"provider_tenant_id": "T1", "provider_app_id": "A1"}
    with Session(engine) as db, db.begin():
        preset_id = _preset(db, ws)
        db.add(_bot(ws, preset_id, **app))
    with (
        pytest.raises(IntegrityError) as exc,
        Session(engine) as db,
        db.begin(),
    ):
        db.add(_bot(ws, preset_id, **app))
    assert _violated(exc) == "uq_agent_channel_bot_provider_tenant_app"
