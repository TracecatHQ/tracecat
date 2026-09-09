"""Tests for the derived-membership migration.

Revision 4134d4ebdc69 changes no schema and no data: it counts legacy
membership rows no role assignment covers and reports them. The legacy tables
stay until the follow-up revision drops them, and the ORM derives membership
from assignments (see tracecat.db.models).
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, select, text
from sqlalchemy.orm import Session

from tests.database import TEST_DB_CONFIG
from tracecat.db.models import Membership, OrganizationMembership

MIGRATION_REVISION = "4134d4ebdc69"
PREVIOUS_REVISION = "c3a17be4d902"
# Columns the migration's uncovered-row SQL reads.
LEGACY_TABLE_COLUMNS = {
    "membership": {"user_id", "workspace_id"},
    "organization_membership": {"user_id", "organization_id"},
}


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for migration tests."""
    yield


def _run_alembic(db_url: str, *args: str) -> str:
    """Run alembic against ``db_url``, returning its combined output."""
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
    return result.stdout + result.stderr


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
def previous_db() -> Iterator[str]:
    """An empty database at the revision before the one under test."""
    for url in _fresh_database("test_membership_previous"):
        _run_alembic(url, "upgrade", PREVIOUS_REVISION)
        yield url


@pytest.fixture(scope="function")
def migration_db() -> Iterator[str]:
    """An empty database migrated up to the revision under test."""
    for url in _fresh_database("test_membership_derived"):
        _run_alembic(url, "upgrade", MIGRATION_REVISION)
        yield url


def _engine(url: str) -> Engine:
    return create_engine(url)


def _seed_org(conn: Connection) -> tuple[uuid.UUID, uuid.UUID]:
    """One org with the two system roles.

    Returns (org_id, workspace_editor_role_id).
    """
    org_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO organization (id, name, slug, is_active) "
            "VALUES (:id, 'Org', :slug, true)"
        ),
        {"id": org_id, "slug": f"org-{org_id.hex[:8]}"},
    )
    editor_role = uuid.uuid4()
    for role_id, slug, name in (
        (editor_role, "workspace-editor", "Workspace Editor"),
        (uuid.uuid4(), "organization-member", "Organization Member"),
    ):
        conn.execute(
            text(
                "INSERT INTO role (id, name, slug, organization_id) "
                "VALUES (:id, :name, :slug, :org)"
            ),
            {"id": role_id, "name": name, "slug": slug, "org": org_id},
        )
    return org_id, editor_role


def _seed_user(conn: Connection) -> uuid.UUID:
    user_id = uuid.uuid4()
    user_role = conn.execute(
        text(
            "SELECT enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'userrole' LIMIT 1"
        )
    ).scalar_one()
    conn.execute(
        text(
            'INSERT INTO "user" (id, email, hashed_password, is_active, '
            "is_superuser, is_verified, role) "
            "VALUES (:id, :email, 'x', true, false, true, :role)"
        ),
        {"id": user_id, "email": f"{user_id.hex[:8]}@example.com", "role": user_role},
    )
    return user_id


def _seed_workspace(conn: Connection, org_id: uuid.UUID) -> uuid.UUID:
    ws_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO workspace (id, name, organization_id) VALUES (:id, :n, :org)"
        ),
        {"id": ws_id, "n": f"ws-{ws_id.hex[:8]}", "org": org_id},
    )
    return ws_id


def _assign(
    conn: Connection,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    role_id: uuid.UUID,
) -> None:
    conn.execute(
        text(
            "INSERT INTO user_role_assignment "
            "(id, organization_id, user_id, workspace_id, role_id) "
            "VALUES (gen_random_uuid(), :org, :u, :ws, :role)"
        ),
        {"org": org_id, "u": user_id, "ws": workspace_id, "role": role_id},
    )


def _presence(session: Session, user_id: uuid.UUID) -> tuple[int, int]:
    """(workspace rows, org rows) the ORM derives for a user."""
    workspaces = session.scalars(
        select(Membership).where(Membership.user_id == user_id)
    ).all()
    orgs = session.scalars(
        select(OrganizationMembership).where(OrganizationMembership.user_id == user_id)
    ).all()
    return len(workspaces), len(orgs)


def test_upgrade_keeps_legacy_tables(migration_db: str) -> None:
    """No schema changes: the tables stay, with the columns the guard reads."""
    engine = _engine(migration_db)
    try:
        with engine.connect() as conn:
            for name, columns in LEGACY_TABLE_COLUMNS.items():
                assert _relkind(conn, name) == "r", f"{name} should still be a table"
                present = set(
                    conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = :name"
                        ),
                        {"name": name},
                    ).scalars()
                )
                assert columns <= present, f"{name} is missing {columns - present}"
    finally:
        engine.dispose()


def _row_counts(conn: Connection) -> dict[str, int]:
    """Row counts for the assignment and legacy tables."""
    return {
        table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in (
            "user_role_assignment",
            "group_role_assignment",
            "membership",
            "organization_membership",
        )
    }


def test_upgrade_changes_no_rows(previous_db: str) -> None:
    """The migration reports; it never inserts, updates or deletes."""
    engine = _engine(previous_db)
    try:
        with engine.begin() as conn:
            org_id, editor_role = _seed_org(conn)
            listed, assigned = _seed_user(conn), _seed_user(conn)
            ws_id = _seed_workspace(conn, org_id)
            conn.execute(
                text("INSERT INTO membership (user_id, workspace_id) VALUES (:u, :w)"),
                {"u": listed, "w": ws_id},
            )
            conn.execute(
                text(
                    "INSERT INTO organization_membership (user_id, organization_id) "
                    "VALUES (:u, :o)"
                ),
                {"u": listed, "o": org_id},
            )
            _assign(conn, org_id, assigned, ws_id, editor_role)
        with engine.connect() as conn:
            before = _row_counts(conn)

        _run_alembic(previous_db, "upgrade", MIGRATION_REVISION)

        with engine.connect() as conn:
            assert _row_counts(conn) == before
        with Session(engine) as session:
            # The uncovered legacy row stays invisible; the assignment shows.
            assert _presence(session, listed) == (0, 0)
            assert _presence(session, assigned) == (1, 0)
    finally:
        engine.dispose()


def test_upgrade_reports_uncovered_rows(previous_db: str) -> None:
    """An uncovered legacy row is counted in a warning, not an error."""
    engine = _engine(previous_db)
    try:
        with engine.begin() as conn:
            org_id, _ = _seed_org(conn)
            user_id = _seed_user(conn)
            ws_id = _seed_workspace(conn, org_id)
            conn.execute(
                text("INSERT INTO membership (user_id, workspace_id) VALUES (:u, :w)"),
                {"u": user_id, "w": ws_id},
            )

        output = _run_alembic(previous_db, "upgrade", MIGRATION_REVISION)

        assert "1 workspace and 0 organization membership row(s)" in output
        assert str(user_id) not in output
        with engine.connect() as conn:
            assert _relkind(conn, "membership") == "r"
    finally:
        engine.dispose()


def test_orm_derives_presence_from_assignment_paths(migration_db: str) -> None:
    """Each path kind lands in exactly the relation it should."""
    engine = _engine(migration_db)
    try:
        with engine.begin() as conn:
            org_id, editor_role = _seed_org(conn)
            user_id = _seed_user(conn)
            ws_a, ws_b = _seed_workspace(conn, org_id), _seed_workspace(conn, org_id)
            group_id = uuid.uuid4()
        with Session(engine) as session:
            conn = session.connection()
            assert _presence(session, user_id) == (0, 0)

            # A workspace-scoped assignment is workspace presence only.
            _assign(conn, org_id, user_id, ws_a, editor_role)
            assert _presence(session, user_id) == (1, 0)

            # An org-wide assignment is org presence only.
            _assign(conn, org_id, user_id, None, editor_role)
            assert _presence(session, user_id) == (1, 1)

            # A group grant reaches its members.
            conn.execute(
                text(
                    'INSERT INTO "group" (id, name, organization_id) '
                    "VALUES (:id, 'Group', :org)"
                ),
                {"id": group_id, "org": org_id},
            )
            conn.execute(
                text("INSERT INTO group_member (group_id, user_id) VALUES (:g, :u)"),
                {"g": group_id, "u": user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO group_role_assignment "
                    "(id, organization_id, group_id, workspace_id, role_id) "
                    "VALUES (gen_random_uuid(), :org, :g, :ws, :role)"
                ),
                {"org": org_id, "g": group_id, "ws": ws_b, "role": editor_role},
            )
            assert _presence(session, user_id) == (2, 1)
            session.rollback()
    finally:
        engine.dispose()


def test_orm_never_reads_the_legacy_tables(migration_db: str) -> None:
    """A legacy row with no assignment is invisible: the tables are unread."""
    for statement in (select(Membership), select(OrganizationMembership)):
        compiled = str(statement.compile())
        assert (
            re.search(r"\bFROM (membership|organization_membership)\b", compiled)
            is None
        )

    engine = _engine(migration_db)
    try:
        with engine.begin() as conn:
            org_id, _ = _seed_org(conn)
            user_id = _seed_user(conn)
            ws_id = _seed_workspace(conn, org_id)
            conn.execute(
                text("INSERT INTO membership (user_id, workspace_id) VALUES (:u, :w)"),
                {"u": user_id, "w": ws_id},
            )
            conn.execute(
                text(
                    "INSERT INTO organization_membership (user_id, organization_id) "
                    "VALUES (:u, :o)"
                ),
                {"u": user_id, "o": org_id},
            )
        with Session(engine) as session:
            assert _presence(session, user_id) == (0, 0)
    finally:
        engine.dispose()


def test_downgrade_is_a_no_op(migration_db: str) -> None:
    _run_alembic(migration_db, "downgrade", PREVIOUS_REVISION)
    engine = _engine(migration_db)
    try:
        with engine.connect() as conn:
            for name in LEGACY_TABLE_COLUMNS:
                assert _relkind(conn, name) == "r"
    finally:
        engine.dispose()
