"""Tests for the agent channel bot, thread link, and channel route migrations."""

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
from sqlalchemy import Engine, create_engine, delete, insert, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.agent.channel_bots.schemas import (
    ChannelBotAppOwner,
    ChannelBotProvider,
    ChannelBotStatus,
    ChannelBotTransport,
)
from tracecat.db.models import (
    AgentChannelBot,
    AgentChannelRoute,
    AgentPreset,
    AgentSession,
    AgentSessionThread,
    Base,
    Organization,
    Workspace,
)

MIGRATION_REVISION = "e20249352688"
# Composite preset key.
PRESET_KEY_REVISION = "43d161879873"
# Token refresh due date; the parent of the route revision.
TOKEN_REFRESH_REVISION = "183d302a8d08"
# Platform installs and channel routes; the models match the schema from here.
ROUTE_REVISION = "11e685c62cfd"
PREVIOUS_REVISION = "ec392d96f2b0"
TABLES = {"agent_channel_bot", "agent_session_thread", "agent_channel_route"}
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
                AgentSessionThread.session_id,
                AgentSessionThread.bot_id,
                AgentSessionThread.provider_thread_id["thread_ts"].astext,
            ).where(AgentSessionThread.workspace_id == ws)
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
                " WHERE c.relrowsecurity"
                " AND c.relname IN"
                " ('agent_channel_bot', 'agent_session_thread', 'agent_channel_route')"
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
                " AND tablename IN"
                " ('agent_channel_bot', 'agent_session_thread', 'agent_channel_route')"
            )
        )
        return set(rows.scalars())


def test_upgrade_matches_the_models_and_downgrades(db_url: str, engine: Engine) -> None:
    _alembic(db_url, "downgrade", PREVIOUS_REVISION)
    assert _model_drift(engine)
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    assert _model_drift(engine)
    _alembic(db_url, "upgrade", PRESET_KEY_REVISION)
    assert _model_drift(engine)
    _alembic(db_url, "upgrade", TOKEN_REFRESH_REVISION)
    assert _model_drift(engine)
    _alembic(db_url, "upgrade", ROUTE_REVISION)
    assert _model_drift(engine) == []
    assert _rls_tables(engine) == TABLES

    _alembic(db_url, "downgrade", TOKEN_REFRESH_REVISION)
    assert _channel_tables(engine) == TABLES - {"agent_channel_route"}
    _alembic(db_url, "downgrade", PREVIOUS_REVISION)
    assert _channel_tables(engine) == set()
    _alembic(db_url, "upgrade", ROUTE_REVISION)
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
) -> AgentSessionThread:
    thread = {
        "provider": "slack",
        "team_id": "T1",
        "channel_id": "C1",
        "thread_ts": thread_ts,
    }
    return AgentSessionThread(
        session_id=session_id,
        workspace_id=workspace_id,
        bot_id=bot_id,
        provider_thread_id=thread,
    )


def _violated(exc: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    diag = getattr(exc.value.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def test_links_stay_in_their_bots_and_sessions_workspace(
    db_url: str, engine: Engine
) -> None:
    _alembic(db_url, "upgrade", ROUTE_REVISION)
    ws, other_ws = _workspace(engine)
    with Session(engine, expire_on_commit=False) as db, db.begin():
        preset_id = _preset(db, ws)
        bot = _bot(ws, preset_id)
        mine, also_mine = _session(ws, preset_id), _session(ws, preset_id)
        foreign = _session(other_ws, preset_id)
        db.add_all([bot, mine, also_mine, foreign])

    def link(
        session_id: uuid.UUID, workspace_id: uuid.UUID = ws, thread_ts: str = "1.1"
    ) -> AgentSessionThread:
        return _link(bot.id, session_id, workspace_id, thread_ts)

    with Session(engine) as db, db.begin():
        db.add(link(mine.id))
    rejected = [
        # One session per thread per bot.
        (link(also_mine.id), "uq_agent_session_thread_bot_id_provider_thread_id"),
        # A link names its bot's workspace, so RLS on the link means the bot's tenant.
        (
            link(foreign.id, other_ws, "1.2"),
            "fk_agent_session_thread_bot_id_workspace_id_agent_channel_bot",
        ),
        # A link never names another workspace's session.
        (
            link(foreign.id, ws, "1.3"),
            "fk_agent_session_thread_session_id_workspace_id_agent_session",
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
    _alembic(db_url, "upgrade", ROUTE_REVISION)
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
    assert _links(engine, ws) == {(session.id, bot.id, "1.1")}


def test_bot_checks_match_the_api_enums(db_url: str, engine: Engine) -> None:
    _alembic(db_url, "upgrade", ROUTE_REVISION)
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
                app_owner=owner.value,
            )
            for provider in ChannelBotProvider
            for status in ChannelBotStatus
            for transport in ChannelBotTransport
            for owner in ChannelBotAppOwner
        )
    for bogus, constraint in (
        ({"provider": "teams"}, "ck_agent_channel_bot_provider"),
        ({"app_owner": "partner"}, "ck_agent_channel_bot_app_owner"),
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
    _alembic(db_url, "upgrade", ROUTE_REVISION)
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


def test_a_bot_never_targets_another_workspaces_preset(
    db_url: str, engine: Engine
) -> None:
    _alembic(db_url, "upgrade", ROUTE_REVISION)
    ws, other_ws = _workspace(engine)
    with Session(engine) as db, db.begin():
        foreign_preset_id = _preset(db, other_ws)
    with (
        pytest.raises(IntegrityError) as exc,
        Session(engine) as db,
        db.begin(),
    ):
        db.add(_bot(ws, foreign_preset_id))
    assert (
        _violated(exc)
        == "fk_agent_channel_bot_agent_preset_id_workspace_id_agent_preset"
    )


def test_rls_isolates_bots_and_links_by_workspace(db_url: str, engine: Engine) -> None:
    """A non-owner role sees and writes only its current workspace's rows."""
    _alembic(db_url, "upgrade", ROUTE_REVISION)
    ws, other_ws = _workspace(engine)
    with Session(engine, expire_on_commit=False) as db, db.begin():
        preset_id, other_preset_id = _preset(db, ws), _preset(db, other_ws)
        bot, other_bot = _bot(ws, preset_id), _bot(other_ws, other_preset_id)
        session, other_session = (
            _session(ws, preset_id),
            _session(other_ws, other_preset_id),
        )
        db.add_all([bot, other_bot, session, other_session])
        db.flush()
        db.add_all(
            [
                _link(bot.id, session.id, ws),
                _link(other_bot.id, other_session.id, other_ws),
            ]
        )
    role = f"channel_rls_{uuid.uuid4().hex[:8]}"
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
            conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            conn.execute(
                text(
                    "GRANT SELECT, INSERT ON agent_channel_bot, agent_session_thread"
                    f' TO "{role}"'
                )
            )

        with engine.connect() as conn, conn.begin():
            conn.execute(text(f'SET LOCAL ROLE "{role}"'))
            conn.execute(
                text("SELECT set_config('app.current_workspace_id', :ws, true)"),
                {"ws": str(ws)},
            )

            def visible() -> tuple[set[uuid.UUID], set[uuid.UUID]]:
                # Other tests share this database; count only this test's rows.
                mine = (ws, other_ws)
                bots = conn.execute(
                    select(AgentChannelBot.id).where(
                        AgentChannelBot.workspace_id.in_(mine)
                    )
                ).scalars()
                links = conn.execute(
                    select(AgentSessionThread.session_id).where(
                        AgentSessionThread.workspace_id.in_(mine)
                    )
                ).scalars()
                return set(bots), set(links)

            assert visible() == ({bot.id}, {session.id})
            with (
                pytest.raises(ProgrammingError, match="row-level security"),
                conn.begin_nested(),
            ):
                conn.execute(
                    insert(AgentChannelBot).values(
                        workspace_id=other_ws,
                        agent_preset_id=other_preset_id,
                        provider="slack",
                        manifest_version=1,
                        encrypted_credentials=b"sealed",
                    )
                )

            conn.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
            assert visible() == ({bot.id, other_bot.id}, {session.id, other_session.id})
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP OWNED BY "{role}"'))
            conn.execute(text(f'DROP ROLE "{role}"'))


def test_bots_from_before_platform_installs_are_customer_owned(
    db_url: str, engine: Engine
) -> None:
    _alembic(db_url, "downgrade", TOKEN_REFRESH_REVISION)
    ws, _ = _workspace(engine)
    with Session(engine) as db, db.begin():
        preset_id = _preset(db, ws)
        bot_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO agent_channel_bot (id, workspace_id, agent_preset_id,"
                " provider, manifest_version, encrypted_credentials)"
                " VALUES (:id, :ws, :preset, 'slack', 1, 'sealed')"
            ),
            {"id": bot_id, "ws": ws, "preset": preset_id},
        )
    _alembic(db_url, "upgrade", ROUTE_REVISION)
    with Session(engine) as db:
        owner = db.scalar(
            select(AgentChannelBot.app_owner).where(AgentChannelBot.id == bot_id)
        )
    assert owner == ChannelBotAppOwner.CUSTOMER


def _route(
    bot_id: uuid.UUID, ws: uuid.UUID, preset_id: uuid.UUID, channel: str = "C1"
) -> AgentChannelRoute:
    return AgentChannelRoute(
        bot_id=bot_id,
        workspace_id=ws,
        provider_channel_id=channel,
        agent_preset_id=preset_id,
    )


def _routes(engine: Engine, ws: uuid.UUID) -> set[tuple[str, uuid.UUID]]:
    with Session(engine) as db:
        rows = db.execute(
            select(
                AgentChannelRoute.provider_channel_id,
                AgentChannelRoute.agent_preset_id,
            ).where(AgentChannelRoute.workspace_id == ws)
        )
        return set(rows.tuples())


def test_routes_stay_in_their_bots_workspace(db_url: str, engine: Engine) -> None:
    _alembic(db_url, "upgrade", ROUTE_REVISION)
    ws, other_ws = _workspace(engine)
    with Session(engine, expire_on_commit=False) as db, db.begin():
        preset_id, routed_id = _preset(db, ws), _preset(db, ws)
        foreign_preset_id = _preset(db, other_ws)
        bot = _bot(ws, preset_id, app_owner="platform")
        db.add(bot)
    with Session(engine) as db, db.begin():
        db.add(_route(bot.id, ws, routed_id))
    rejected = [
        # One agent per channel of a bot.
        (_route(bot.id, ws, preset_id), "pk_agent_channel_route"),
        # A route names its bot's workspace, so RLS on the route means the bot's tenant.
        (
            _route(bot.id, other_ws, foreign_preset_id, "C2"),
            "fk_agent_channel_route_bot_id_workspace_id_agent_channel_bot",
        ),
        # A route never targets another workspace's agent.
        (
            _route(bot.id, ws, foreign_preset_id, "C3"),
            # The convention's name passes 63 bytes, so it is truncated with a hash.
            "fk_agent_channel_route_agent_preset_id_workspace_id_age_555a",
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
    assert _routes(engine, ws) == {("C1", routed_id)}

    # Deleting the routed agent drops its routes; the channel falls back to the default.
    with Session(engine) as db, db.begin():
        db.execute(delete(AgentPreset).where(AgentPreset.id == routed_id))
    assert _routes(engine, ws) == set()
    with Session(engine) as db, db.begin():
        db.add(_route(bot.id, ws, preset_id))
    with Session(engine) as db, db.begin():
        db.execute(delete(AgentChannelBot).where(AgentChannelBot.id == bot.id))
    assert _routes(engine, ws) == set()
