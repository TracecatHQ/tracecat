"""Tests for the additive SCIM connection credential migration."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, select, text, update
from sqlalchemy.exc import IntegrityError

from tests.database import TEST_DB_CONFIG
from tracecat.authz.enums import ScimConnectionStatus
from tracecat.db.models import ScimConnection

MIGRATION_REVISION = "a667d946cca1"
PREVIOUS_REVISION = "e847d14eeb86"
NEW_TABLE = "scim_connection"
RLS_POLICY = "rls_policy_scim_connection"


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for migration tests."""
    yield


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


def _fresh_database(prefix: str) -> Iterator[str]:
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT"
    )
    db_name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    try:
        with default_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        yield TEST_DB_CONFIG.test_url_sync.replace(TEST_DB_CONFIG.test_db_name, db_name)
    finally:
        with default_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        default_engine.dispose()


@pytest.fixture(scope="function")
def migration_db() -> Iterator[str]:
    """An empty database migrated up to the revision under test."""
    for url in _fresh_database("test_scim_connection"):
        _run_alembic(url, "upgrade", MIGRATION_REVISION)
        yield url


def _engine(url: str) -> Engine:
    return create_engine(url)


def _table_exists(conn: Connection, name: str) -> bool:
    return (
        conn.execute(
            text(
                "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = :name"
            ),
            {"name": name},
        ).scalar_one_or_none()
        is not None
    )


def _seed_org(conn: Connection) -> uuid.UUID:
    org_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO organization (id, name, slug, is_active) "
            "VALUES (:id, 'Org', :slug, true)"
        ),
        {"id": org_id, "slug": f"org-{org_id.hex[:8]}"},
    )
    return org_id


def _seed_connection(conn: Connection, org_id: uuid.UUID) -> None:
    conn.execute(
        text(
            "INSERT INTO scim_connection "
            "(id, organization_id, key_id, hashed, salt, preview) "
            "VALUES (gen_random_uuid(), :org, :key_id, 'h', 's', 'tc_scim_sk_...abcd')"
        ),
        {"org": org_id, "key_id": uuid.uuid4().hex[:12]},
    )


def test_upgrade_creates_the_connection_table(migration_db: str) -> None:
    engine = _engine(migration_db)
    try:
        with engine.connect() as conn:
            assert _table_exists(conn, NEW_TABLE)
    finally:
        engine.dispose()


def test_tenant_isolation_policy_is_applied(migration_db: str) -> None:
    """The table carries its RLS policy, not only its columns."""
    engine = _engine(migration_db)
    try:
        with engine.connect() as conn:
            policies = (
                conn.execute(
                    text("SELECT policyname FROM pg_policies WHERE tablename = :t"),
                    {"t": NEW_TABLE},
                )
                .scalars()
                .all()
            )
            assert RLS_POLICY in policies, f"missing policy. Found: {policies}"

            enabled = conn.execute(
                text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"),
                {"t": NEW_TABLE},
            ).scalar_one()
            assert enabled is True
    finally:
        engine.dispose()


def test_one_connection_per_organization(migration_db: str) -> None:
    """The unique constraint is what makes a connection per-organization."""
    engine = _engine(migration_db)
    try:
        with engine.begin() as conn:
            org_id = _seed_org(conn)
            _seed_connection(conn, org_id)

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _seed_connection(conn, org_id)
    finally:
        engine.dispose()


def test_deleting_the_organization_cascades(migration_db: str) -> None:
    engine = _engine(migration_db)
    try:
        with engine.begin() as conn:
            org_id = _seed_org(conn)
            _seed_connection(conn, org_id)

        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM organization WHERE id = :id"), {"id": org_id}
            )
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT count(*) FROM scim_connection")).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_downgrade_removes_everything_the_upgrade_added(migration_db: str) -> None:
    """The additive migration round-trips: down leaves no trace, up restores it."""
    engine = _engine(migration_db)
    try:
        _run_alembic(migration_db, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            assert not _table_exists(conn, NEW_TABLE)

        _run_alembic(migration_db, "upgrade", MIGRATION_REVISION)
        with engine.connect() as conn:
            assert _table_exists(conn, NEW_TABLE)
            policies = (
                conn.execute(
                    text("SELECT policyname FROM pg_policies WHERE tablename = :t"),
                    {"t": NEW_TABLE},
                )
                .scalars()
                .all()
            )
            assert RLS_POLICY in policies
    finally:
        engine.dispose()


def test_status_round_trips_as_enum_on_existing_schema(migration_db: str) -> None:
    """ORM enum conversion preserves legacy lowercase VARCHAR values."""
    engine = _engine(migration_db)
    try:
        with engine.begin() as conn:
            org_id = _seed_org(conn)
            _seed_connection(conn, org_id)
            for status in ScimConnectionStatus:
                conn.execute(
                    text("UPDATE scim_connection SET status = :status"),
                    {"status": status.value},
                )
                assert conn.scalar(select(ScimConnection.status)) is status
                conn.execute(update(ScimConnection).values(status=status))
                assert (
                    conn.scalar(text("SELECT status FROM scim_connection"))
                    == status.value
                )
    finally:
        engine.dispose()
