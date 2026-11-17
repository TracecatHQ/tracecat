"""Tests for forcing RLS on dynamic workspace tables (b0debed4e0c6).

Invariants:
- Upgrade forces RLS on every table in `tables_*` and `custom_fields_*`
  schemas and nowhere else; downgrade removes FORCE again.
- Once forced, a non-superuser, non-BYPASSRLS owner is filtered by the
  workspace policy.
- Alembic's migration connection sets `app.rls_bypass=on` for its whole
  session, so a migration running as that owner still sees every row.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Connection, create_engine, make_url, text
from sqlalchemy.engine import Engine

from alembic import command
from tests.database import TEST_DB_CONFIG
from tracecat.db import migration_lock
from tracecat.identifiers.workflow import WorkspaceUUID

MIGRATION_REVISION = "b0debed4e0c6"
PREVIOUS_REVISION = "4a17af960e05"
ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"
TABLE_NAME = "probe"
POLICY_EXPR = (
    "current_setting('app.rls_bypass', true) = 'on' "
    'OR "__tc_workspace_id" = '
    "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"
)


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for migration tests."""
    yield


@dataclass(frozen=True, slots=True)
class ForceRlsDb:
    db_url: str
    owner_url: str
    owner: str
    workspace_id: uuid.UUID
    dynamic_schemas: tuple[str, str]
    decoy_schema: str
    row_id: uuid.UUID


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
            f"alembic {' '.join(args)} failed:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def _force_flag(engine: Engine, schema_name: str) -> bool:
    with engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema_name AND c.relname = :table_name
                """
            ),
            {"schema_name": schema_name, "table_name": TABLE_NAME},
        ).scalar_one()


def _owner_row_ids(
    engine: Engine,
    *,
    owner: str,
    schema_name: str,
    workspace_id: uuid.UUID | None,
    bypass: bool = False,
) -> list[uuid.UUID]:
    with engine.begin() as conn:
        conn.execute(text(f'SET LOCAL ROLE "{owner}"'))
        conn.execute(
            text(
                """
                SELECT
                    set_config('app.current_workspace_id', :workspace_id, true),
                    set_config('app.rls_bypass', :bypass, true)
                """
            ),
            {
                "workspace_id": str(workspace_id) if workspace_id else "",
                "bypass": "on" if bypass else "off",
            },
        )
        return list(
            conn.execute(
                text(f'SELECT id FROM "{schema_name}"."{TABLE_NAME}"')
            ).scalars()
        )


@pytest.fixture
def force_rls_db() -> Iterator[ForceRlsDb]:
    """A database at the previous revision with RLS-enabled dynamic tables.

    Each table has one row, a workspace policy, and a LOGIN owner role
    without superuser or BYPASSRLS. A decoy schema that only looks like a
    dynamic schema must not be forced.
    """
    sys_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT"
    )
    suffix = uuid.uuid4().hex[:8]
    db_name = f"test_force_rls_{suffix}"
    owner = f"rls_mig_owner_{suffix}"
    owner_password = uuid.uuid4().hex
    terminate = text(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = :db_name AND pid <> pg_backend_pid()"
    )

    workspace_id = uuid.uuid4()
    short = WorkspaceUUID.new(workspace_id).short()
    dynamic_schemas = (f"tables_{short}", f"custom_fields_{short}")
    decoy_schema = f"tablesx{short}"
    row_id = uuid.uuid4()

    try:
        with sys_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            conn.execute(
                text(
                    f'CREATE ROLE "{owner}" LOGIN NOSUPERUSER NOBYPASSRLS '
                    f"PASSWORD '{owner_password}'"
                )
            )

        db_url = TEST_DB_CONFIG.test_url_sync.replace(
            TEST_DB_CONFIG.test_db_name, db_name
        )
        owner_url = (
            make_url(db_url)
            .set(username=owner, password=owner_password)
            .render_as_string(hide_password=False)
        )

        engine = create_engine(db_url)
        try:
            with engine.begin() as conn:
                for schema_name in (*dynamic_schemas, decoy_schema):
                    qualified = f'"{schema_name}"."{TABLE_NAME}"'
                    conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))
                    conn.execute(
                        text(
                            f"""
                            CREATE TABLE {qualified} (
                                id UUID PRIMARY KEY,
                                "__tc_workspace_id" UUID NOT NULL
                            )
                            """
                        )
                    )
                    conn.execute(
                        text(f"INSERT INTO {qualified} VALUES (:id, :ws)"),
                        {"id": row_id, "ws": workspace_id},
                    )
                    conn.execute(
                        text(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
                    )
                    conn.execute(
                        text(
                            f"CREATE POLICY rls_policy_dynamic_workspace ON {qualified} "
                            f"FOR ALL USING ({POLICY_EXPR}) WITH CHECK ({POLICY_EXPR})"
                        )
                    )
                    conn.execute(
                        text(f'GRANT USAGE ON SCHEMA "{schema_name}" TO "{owner}"')
                    )
                    conn.execute(text(f'ALTER TABLE {qualified} OWNER TO "{owner}"'))
        finally:
            engine.dispose()

        _run_alembic(db_url, "stamp", PREVIOUS_REVISION)
        engine = create_engine(db_url)
        try:
            with engine.begin() as conn:
                conn.execute(text(f'GRANT SELECT ON alembic_version TO "{owner}"'))
        finally:
            engine.dispose()

        yield ForceRlsDb(
            db_url=db_url,
            owner_url=owner_url,
            owner=owner,
            workspace_id=workspace_id,
            dynamic_schemas=dynamic_schemas,
            decoy_schema=decoy_schema,
            row_id=row_id,
        )
    finally:
        with sys_engine.connect() as conn:
            conn.execute(terminate, {"db_name": db_name})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            conn.execute(text(f'DROP ROLE IF EXISTS "{owner}"'))
        sys_engine.dispose()


class TestForceDynamicRlsMigration:
    def test_upgrade_forces_rls_and_downgrade_removes_it(
        self, force_rls_db: ForceRlsDb
    ) -> None:
        other_workspace = uuid.uuid4()
        engine = create_engine(force_rls_db.db_url)
        try:
            for schema_name in force_rls_db.dynamic_schemas:
                # Before: the owner skips the policy.
                assert _force_flag(engine, schema_name) is False
                assert _owner_row_ids(
                    engine,
                    owner=force_rls_db.owner,
                    schema_name=schema_name,
                    workspace_id=other_workspace,
                ) == [force_rls_db.row_id]

            _run_alembic(force_rls_db.db_url, "upgrade", MIGRATION_REVISION)

            assert _force_flag(engine, force_rls_db.decoy_schema) is False
            for schema_name in force_rls_db.dynamic_schemas:
                assert _force_flag(engine, schema_name) is True
                for workspace_id, bypass, expected in (
                    (force_rls_db.workspace_id, False, [force_rls_db.row_id]),
                    (other_workspace, False, []),
                    (None, False, []),
                    (other_workspace, True, [force_rls_db.row_id]),
                ):
                    assert (
                        _owner_row_ids(
                            engine,
                            owner=force_rls_db.owner,
                            schema_name=schema_name,
                            workspace_id=workspace_id,
                            bypass=bypass,
                        )
                        == expected
                    )

            _run_alembic(force_rls_db.db_url, "downgrade", PREVIOUS_REVISION)

            for schema_name in force_rls_db.dynamic_schemas:
                assert _force_flag(engine, schema_name) is False
                assert _owner_row_ids(
                    engine,
                    owner=force_rls_db.owner,
                    schema_name=schema_name,
                    workspace_id=other_workspace,
                ) == [force_rls_db.row_id]
        finally:
            engine.dispose()

    def test_migration_connection_sees_rows_of_forced_tables(
        self, force_rls_db: ForceRlsDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _run_alembic(force_rls_db.db_url, "upgrade", MIGRATION_REVISION)

        # Run Alembic in-process as the forced tables' owner, and read them on
        # the migration connection once its transaction has committed.
        seen: dict[str, list[uuid.UUID]] = {}
        release_lock = migration_lock.release_migration_advisory_lock

        def read_then_release(connection: Connection) -> None:
            for schema_name in force_rls_db.dynamic_schemas:
                seen[schema_name] = list(
                    connection.execute(
                        text(f'SELECT id FROM "{schema_name}"."{TABLE_NAME}"')
                    ).scalars()
                )
            release_lock(connection)

        monkeypatch.setattr(
            migration_lock, "release_migration_advisory_lock", read_then_release
        )
        monkeypatch.setenv("TRACECAT__DB_URI", force_rls_db.owner_url)
        alembic_config = Config()
        alembic_config.set_main_option("script_location", str(ALEMBIC_DIR))
        command.current(alembic_config)

        assert seen == {
            schema_name: [force_rls_db.row_id]
            for schema_name in force_rls_db.dynamic_schemas
        }
