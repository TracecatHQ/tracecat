"""Tests for the additive managed resource side-table migration."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.db.models import Base

MIGRATION_REVISION = "21042b4645f1"
PREVIOUS_REVISION = "002febfa86c3"
TABLES = ("managed_resource", "managed_target", "managed_reference_match")


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
    name = f"test_managed_resources_{uuid.uuid4().hex[:8]}"
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


def _existing_tables(conn: Connection) -> set[str]:
    inspector = inspect(conn)
    return {table for table in TABLES if inspector.has_table(table)}


def _include_only_managed_tables(
    _object: object,
    name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: object,
) -> bool:
    return type_ != "table" or name in TABLES


def test_upgrade_matches_the_model_and_downgrade_removes_the_tables(
    db_url: str,
) -> None:
    engine = create_engine(db_url, poolclass=NullPool)
    try:
        _alembic(db_url, "upgrade", MIGRATION_REVISION)
        with engine.connect() as conn:
            assert _existing_tables(conn) == set(TABLES)
            context = MigrationContext.configure(
                conn,
                opts={
                    "include_object": _include_only_managed_tables,
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
            for table in TABLES:
                assert conn.execute(
                    text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"),
                    {"t": table},
                ).scalar_one()

        _alembic(db_url, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            assert _existing_tables(conn) == set()
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_policies"
                        " WHERE tablename = ANY(:tables)"
                    ),
                    {"tables": list(TABLES)},
                ).scalar_one()
                == 0
            )

        # The downgrade left nothing behind that blocks a second upgrade.
        _alembic(db_url, "upgrade", MIGRATION_REVISION)
        with engine.connect() as conn:
            assert _existing_tables(conn) == set(TABLES)
    finally:
        engine.dispose()


def _seed_workspace(conn: Connection, org_id: uuid.UUID) -> uuid.UUID:
    workspace_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO workspace (id, organization_id, name)"
            " VALUES (:id, :org, 'rls-test')"
        ),
        {"id": workspace_id, "org": org_id},
    )
    return workspace_id


def _insert_resource(conn: Connection, workspace_id: uuid.UUID) -> uuid.UUID:
    row_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO managed_resource"
            " (id, workspace_id, resource_type, local_id, source_id, source_path,"
            "  deployed_tag, deployed_commit_sha)"
            " VALUES (:id, :ws, 'workflow', :local, :source, 'workflows/a.yml',"
            "  'v1.0.0', 'abc')"
        ),
        {"id": row_id, "ws": workspace_id, "local": uuid.uuid4(), "source": row_id.hex},
    )
    return row_id


def _insert_target(conn: Connection, workspace_id: uuid.UUID) -> uuid.UUID:
    row_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO managed_target (id, workspace_id, pinned_tag)"
            " VALUES (:id, :ws, 'v1.0.0')"
        ),
        {"id": row_id, "ws": workspace_id},
    )
    return row_id


def _insert_reference_match(conn: Connection, workspace_id: uuid.UUID) -> uuid.UUID:
    row_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO managed_reference_match"
            " (id, workspace_id, reference_kind, source_key)"
            " VALUES (:id, :ws, 'secret_store', :key)"
        ),
        {"id": row_id, "ws": workspace_id, "key": row_id.hex},
    )
    return row_id


_INSERTERS = {
    "managed_resource": _insert_resource,
    "managed_target": _insert_target,
    "managed_reference_match": _insert_reference_match,
}


@pytest.mark.parametrize("table", TABLES)
def test_tenant_sessions_are_read_only(db_url: str, table: str) -> None:
    """A tenant role reads its workspace's rows but cannot write any row."""
    _alembic(db_url, "upgrade", MIGRATION_REVISION)
    engine = create_engine(db_url, poolclass=NullPool)
    role = f"managed_rls_{uuid.uuid4().hex[:8]}"
    org_id = uuid.uuid4()
    insert = _INSERTERS[table]
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO organization (id, name, slug, is_active)"
                    " VALUES (:id, 'RLS test', :slug, true)"
                ),
                {"id": org_id, "slug": f"rls-{org_id.hex[:8]}"},
            )
            ws_a = _seed_workspace(conn, org_id)
            ws_b = _seed_workspace(conn, org_id)
            row_a = insert(conn, ws_a)
            row_b = insert(conn, ws_b)
            conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
            conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            conn.execute(
                text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{role}"')
            )
            conn.execute(
                text(f'GRANT USAGE ON SEQUENCE {table}_surrogate_id_seq TO "{role}"')
            )

        with engine.connect() as conn, conn.begin():
            conn.execute(text(f'SET LOCAL ROLE "{role}"'))
            conn.execute(
                text("SELECT set_config('app.current_workspace_id', :ws, true)"),
                {"ws": str(ws_a)},
            )
            visible = set(conn.execute(text(f"SELECT id FROM {table}")).scalars())
            assert visible == {row_a}

            with pytest.raises(ProgrammingError, match="row-level security"):
                with conn.begin_nested():
                    insert(conn, ws_a)
            deleted = conn.execute(
                text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_a}
            )
            assert deleted.rowcount == 0
            updated = conn.execute(
                text(f"UPDATE {table} SET updated_at = now() WHERE id = :id"),
                {"id": row_a},
            )
            assert updated.rowcount == 0

            conn.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
            visible = set(conn.execute(text(f"SELECT id FROM {table}")).scalars())
            assert visible == {row_a, row_b}
    finally:
        with engine.begin() as conn:
            role_exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
            ).first()
            if role_exists is not None:
                conn.execute(text(f'DROP OWNED BY "{role}"'))
                conn.execute(text(f'DROP ROLE "{role}"'))
        engine.dispose()
