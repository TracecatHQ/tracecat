"""Tests for the expand-phase membership migration and assignment write RLS.

The 2bf069003a77 migration leaves the ``membership`` and
``organization_membership`` tables in place and unwritten; the contract
revision drops them. It also splits the assignment RLS policy so a
workspace-scoped session cannot write org-wide assignments.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, Engine, create_engine, select, text
from sqlalchemy.exc import ProgrammingError

from tests.database import TEST_DB_CONFIG
from tracecat.db.models import Membership
from tracecat.db.tenant_rls import (
    disable_assignment_split_table_rls,
    enable_assignment_split_table_rls,
)

MIGRATION_REVISION = "2bf069003a77"
PREVIOUS_REVISION = "44d7e75b6f4c"
LEGACY_TABLES = ("membership", "organization_membership")
# Mirrors WORKSPACE_INDEXES in the migration; alembic versions aren't importable.
WORKSPACE_INDEX_NAMES = (
    "ix_user_role_assignment_workspace_id",
    "ix_group_role_assignment_workspace_id",
    "ix_group_member_group_id",
)


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


def _relkind(conn: Connection, name: str) -> str | None:
    """'r' for an ordinary table, 'v' for a view, None when absent."""
    return conn.execute(
        text(
            "SELECT relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = :name"
        ),
        {"name": name},
    ).scalar_one_or_none()


@pytest.fixture(scope="function")
def migration_db() -> Iterator[str]:
    """An empty database migrated up to the revision under test."""
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT"
    )
    db_name = f"test_membership_views_{uuid.uuid4().hex[:8]}"
    try:
        with default_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        url = TEST_DB_CONFIG.test_url_sync.replace(TEST_DB_CONFIG.test_db_name, db_name)
        _run_alembic(url, "upgrade", MIGRATION_REVISION)
        yield url
    finally:
        with default_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        default_engine.dispose()


def test_upgrade_creates_workspace_indexes(migration_db: str) -> None:
    """The workspace-leading assignment indexes back member listing."""
    engine = create_engine(migration_db)
    try:
        with engine.connect() as conn:
            for name in WORKSPACE_INDEX_NAMES:
                assert (
                    conn.execute(
                        text(
                            "SELECT 1 FROM pg_indexes "
                            "WHERE schemaname = 'public' AND indexname = :name"
                        ),
                        {"name": name},
                    ).scalar_one_or_none()
                    == 1
                ), f"{name} should exist"
    finally:
        engine.dispose()


def test_downgrade_keeps_legacy_tables_and_drops_indexes(migration_db: str) -> None:
    """Downgrade only reverses the indexes and RLS swap; tables were never dropped."""
    _run_alembic(migration_db, "downgrade", PREVIOUS_REVISION)
    engine = create_engine(migration_db)
    try:
        with engine.connect() as conn:
            for name in LEGACY_TABLES:
                assert _relkind(conn, name) == "r", f"{name} should still be a table"
            for name in WORKSPACE_INDEX_NAMES:
                assert (
                    conn.execute(
                        text(
                            "SELECT 1 FROM pg_indexes "
                            "WHERE schemaname = 'public' AND indexname = :name"
                        ),
                        {"name": name},
                    ).scalar_one_or_none()
                    is None
                ), f"{name} should be dropped"
    finally:
        engine.dispose()


def test_membership_orm_alias_shadows_the_legacy_table(migration_db: str) -> None:
    """The ORM's `.subquery("membership")` alias must not read the legacy table."""
    engine = create_engine(migration_db)
    try:
        compiled = str(select(Membership).compile(engine))
        # A derived-table alias, not a reference to the legacy table.
        assert ") AS membership" in compiled
        assert "FROM membership" not in compiled
        with engine.connect() as conn:
            conn.execute(select(Membership).limit(0))
    finally:
        engine.dispose()


@pytest.fixture(scope="function")
def rls_db() -> Iterator[tuple[str, str]]:
    """A database with a split-policy assignment table and a non-superuser role."""
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT"
    )
    db_name = f"test_assignment_rls_{uuid.uuid4().hex[:8]}"
    role_name = f"assignment_writer_{uuid.uuid4().hex[:8]}"
    try:
        with default_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            conn.execute(text(f'CREATE ROLE "{role_name}" LOGIN'))
        url = TEST_DB_CONFIG.test_url_sync.replace(TEST_DB_CONFIG.test_db_name, db_name)
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE user_role_assignment ("
                    "  id uuid PRIMARY KEY,"
                    "  organization_id uuid NOT NULL,"
                    "  workspace_id uuid,"
                    "  user_id uuid NOT NULL"
                    ")"
                )
            )
            conn.execute(
                text(enable_assignment_split_table_rls("user_role_assignment"))
            )
            conn.execute(text(f'GRANT ALL ON user_role_assignment TO "{role_name}"'))
        engine.dispose()
        yield url, role_name
    finally:
        with default_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
            conn.execute(text(f'DROP ROLE IF EXISTS "{role_name}"'))
        default_engine.dispose()


def _insert_assignment(
    engine: Engine,
    role_name: str,
    *,
    org_id: uuid.UUID,
    session_workspace: uuid.UUID | None,
    row_workspace: uuid.UUID | None,
) -> bool:
    """Attempt one insert under an RLS context; True when the policy allows it."""
    with engine.connect() as conn:
        conn.execute(text(f'SET ROLE "{role_name}"'))
        conn.execute(
            text(
                "SELECT set_config('app.current_org_id', :org, true),"
                " set_config('app.current_workspace_id', :ws, true),"
                " set_config('app.rls_bypass', 'off', true)"
            ),
            {
                "org": str(org_id),
                "ws": "" if session_workspace is None else str(session_workspace),
            },
        )
        try:
            conn.execute(
                text(
                    "INSERT INTO user_role_assignment"
                    " (id, organization_id, workspace_id, user_id)"
                    " VALUES (:id, :org, :ws, :user)"
                ),
                {
                    "id": uuid.uuid4(),
                    "org": org_id,
                    "ws": row_workspace,
                    "user": uuid.uuid4(),
                },
            )
            return True
        except ProgrammingError as exc:
            if not isinstance(exc.orig, InsufficientPrivilege):
                raise
            return False


def test_workspace_session_cannot_insert_org_wide_assignment(
    rls_db: tuple[str, str],
) -> None:
    """A workspace-scoped session must not create an org-wide assignment."""
    url, role_name = rls_db
    org_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(url)
    try:
        assert not _insert_assignment(
            engine,
            role_name,
            org_id=org_id,
            session_workspace=workspace_id,
            row_workspace=None,
        )
        # The same session may still write inside its own workspace.
        assert _insert_assignment(
            engine,
            role_name,
            org_id=org_id,
            session_workspace=workspace_id,
            row_workspace=workspace_id,
        )
    finally:
        engine.dispose()


def test_org_only_session_can_insert_org_wide_assignment(
    rls_db: tuple[str, str],
) -> None:
    """Org-admin routes run without a workspace context and must keep working."""
    url, role_name = rls_db
    org_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(url)
    try:
        assert _insert_assignment(
            engine,
            role_name,
            org_id=org_id,
            session_workspace=None,
            row_workspace=None,
        )
        # Org admins also assign workspace-scoped roles from the same context.
        assert _insert_assignment(
            engine,
            role_name,
            org_id=org_id,
            session_workspace=None,
            row_workspace=workspace_id,
        )
    finally:
        engine.dispose()


def test_workspace_session_cannot_insert_into_another_workspace(
    rls_db: tuple[str, str],
) -> None:
    """The workspace clause still pins writes to the session's own workspace."""
    url, role_name = rls_db
    org_id = uuid.uuid4()
    engine = create_engine(url)
    try:
        assert not _insert_assignment(
            engine,
            role_name,
            org_id=org_id,
            session_workspace=uuid.uuid4(),
            row_workspace=uuid.uuid4(),
        )
    finally:
        engine.dispose()


def test_disable_split_policy_removes_every_policy(rls_db: tuple[str, str]) -> None:
    """Downgrade must leave no policy behind on the assignment table."""
    url, _ = rls_db
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(disable_assignment_split_table_rls("user_role_assignment"))
            )
        with engine.connect() as conn:
            remaining = conn.execute(
                text(
                    "SELECT count(*) FROM pg_policies"
                    " WHERE tablename = 'user_role_assignment'"
                )
            ).scalar_one()
            assert remaining == 0
    finally:
        engine.dispose()
