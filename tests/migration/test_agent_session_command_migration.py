"""Tests for the additive ``agent_session_command`` migration."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from collections.abc import Iterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.agent.control.enums import CommandState, CommandType
from tracecat.db.models import Base

MIGRATION_REVISION = "b99074fdcf3a"
PREVIOUS_REVISION = "c4e8a2f6d9b1"
TABLE = "agent_session_command"


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
    name = f"test_session_command_{uuid.uuid4().hex[:8]}"
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


def _table_exists(conn: Connection) -> bool:
    return inspect(conn).has_table(TABLE)


def _include_only_command_table(
    _object: object,
    name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: object,
) -> bool:
    return type_ != "table" or name == TABLE


def test_upgrade_matches_the_model_and_downgrade_removes_the_table(
    db_url: str,
) -> None:
    engine = create_engine(db_url, poolclass=NullPool)
    try:
        _alembic(db_url, "upgrade", MIGRATION_REVISION)
        with engine.connect() as conn:
            assert _table_exists(conn)
            context = MigrationContext.configure(
                conn,
                opts={
                    "include_object": _include_only_command_table,
                    "compare_type": True,
                    "compare_server_default": True,
                },
            )
            # alembic_postgresql_enum, once imported by any test, adds
            # database-wide enum diffs that `include_object` cannot filter.
            diffs = [
                diff
                for diff in compare_metadata(context, Base.metadata)
                if "enum" not in str(diff[0])
            ]
            assert diffs == []
            assert conn.execute(
                text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"),
                {"t": TABLE},
            ).scalar_one()
            checks = dict(
                conn.execute(
                    text(
                        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                        " WHERE conrelid = CAST(:t AS regclass) AND contype = 'c'"
                    ),
                    {"t": TABLE},
                )
                .tuples()
                .all()
            )
            # Autogenerate ignores CHECK constraints: a new enum member must come
            # with a migration that widens the matching constraint.
            assert checks.keys() == {
                "ck_agent_session_command_type",
                "ck_agent_session_command_state",
                "ck_agent_session_command_payload_type",
            }
            assert {
                name: set(re.findall(r"'(\w+)'", checks[name]))
                for name in (
                    "ck_agent_session_command_type",
                    "ck_agent_session_command_state",
                )
            } == {
                "ck_agent_session_command_type": set(CommandType),
                "ck_agent_session_command_state": set(CommandState),
            }

        _alembic(db_url, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            assert not _table_exists(conn)
            assert (
                conn.execute(
                    text("SELECT count(*) FROM pg_policies WHERE tablename = :t"),
                    {"t": TABLE},
                ).scalar_one()
                == 0
            )

        # The downgrade left nothing behind that blocks a second upgrade.
        _alembic(db_url, "upgrade", MIGRATION_REVISION)
        with engine.connect() as conn:
            assert _table_exists(conn)
    finally:
        engine.dispose()


def _seed_workspace(conn: Connection, org_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """A workspace with one root session; returns their IDs."""
    workspace_id, session_id = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO workspace (id, organization_id, name)"
            " VALUES (:id, :org, 'rls-test')"
        ),
        {"id": workspace_id, "org": org_id},
    )
    conn.execute(
        text(
            "INSERT INTO agent_session (id, workspace_id, title, entity_type, entity_id)"
            " VALUES (:id, :ws, 'Test', 'workspace_chat', :ws)"
        ),
        {"id": session_id, "ws": workspace_id},
    )
    return workspace_id, session_id


def _insert_command(
    conn: Connection,
    workspace_id: uuid.UUID,
    root_session_id: uuid.UUID,
    seq: int = 1,
    payload: str = '{"type": "steer", "text": "hi"}',
) -> uuid.UUID:
    command_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO agent_session_command"
            " (id, root_session_id, session_id, workspace_id, request_id, seq,"
            "  type, payload, payload_hash, state)"
            " VALUES (:id, :root, :root, :ws, :req, :seq, 'steer', CAST(:payload AS jsonb),"
            "  'h', 'accepted')"
        ),
        {
            "id": command_id,
            "root": root_session_id,
            "ws": workspace_id,
            "req": command_id.hex,
            "seq": seq,
            "payload": payload,
        },
    )
    return command_id


def test_rls_isolates_commands_by_workspace(db_url: str) -> None:
    """A non-owner role sees and writes only its current workspace's commands."""
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    engine = create_engine(db_url, poolclass=NullPool)
    role = f"command_rls_{uuid.uuid4().hex[:8]}"
    org_id = uuid.uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO organization (id, name, slug, is_active)"
                    " VALUES (:id, 'RLS test', :slug, true)"
                ),
                {"id": org_id, "slug": f"rls-{org_id.hex[:8]}"},
            )
            ws_a, root_a = _seed_workspace(conn, org_id)
            ws_b, root_b = _seed_workspace(conn, org_id)
            command_a = _insert_command(conn, ws_a, root_a)
            command_b = _insert_command(conn, ws_b, root_b)
            conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
            conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            conn.execute(text(f'GRANT SELECT, INSERT ON {TABLE} TO "{role}"'))

        with engine.connect() as conn, conn.begin():
            conn.execute(text(f'SET LOCAL ROLE "{role}"'))
            conn.execute(
                text("SELECT set_config('app.current_workspace_id', :ws, true)"),
                {"ws": str(ws_a)},
            )
            visible = set(conn.execute(text(f"SELECT id FROM {TABLE}")).scalars())
            assert visible == {command_a}
            assert command_b not in visible

            own = _insert_command(conn, ws_a, root_a, seq=2)
            # Any writer, not only the store, must keep the payload's type in
            # step with the type column.
            with pytest.raises(IntegrityError, match="payload_type"):
                with conn.begin_nested():
                    _insert_command(
                        conn, ws_a, root_a, seq=3, payload='{"type": "abort"}'
                    )
            with pytest.raises(ProgrammingError, match="row-level security"):
                with conn.begin_nested():
                    _insert_command(conn, ws_b, root_b, seq=2)

            conn.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
            visible = set(conn.execute(text(f"SELECT id FROM {TABLE}")).scalars())
            assert visible == {command_a, own, command_b}
    finally:
        with engine.begin() as conn:
            role_exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
            ).first()
            if role_exists is not None:
                conn.execute(text(f'DROP OWNED BY "{role}"'))
                conn.execute(text(f'DROP ROLE "{role}"'))
        engine.dispose()
