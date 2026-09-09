"""Tests for the membership derived membership and rolling-version compatibility."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from tests.database import TEST_DB_CONFIG
from tracecat.db.models import (
    LegacyMembership,
    LegacyOrganizationMembership,
    Membership,
    OrganizationMembership,
)

MIGRATION_REVISION = "4134d4ebdc69"
PREVIOUS_REVISION = "526f867f6a75"
# Columns the migration's backfill and guard SQL read.
LEGACY_TABLE_COLUMNS = {
    "membership": {"user_id", "workspace_id"},
    "organization_membership": {"user_id", "organization_id"},
}


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
    """One org with the two system roles the backfill needs.

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
    """Membership visible to assignment-derived readers."""
    workspaces = session.scalars(
        select(Membership).where(Membership.user_id == user_id)
    ).all()
    orgs = session.scalars(
        select(OrganizationMembership).where(OrganizationMembership.user_id == user_id)
    ).all()
    return len(workspaces), len(orgs)


def test_upgrade_keeps_legacy_tables(migration_db: str) -> None:
    """Both legacy tables and their original columns remain available."""
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


def test_upgrade_backfills_every_legacy_member(previous_db: str) -> None:
    """A legacy row with no assignment gains one; a drifted user keeps theirs."""
    engine = _engine(previous_db)
    try:
        with engine.begin() as conn:
            org_id, editor_role = _seed_org(conn)
            listed, drifted = _seed_user(conn), _seed_user(conn)
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
            _assign(conn, org_id, drifted, ws_id, editor_role)

        _run_alembic(previous_db, "upgrade", MIGRATION_REVISION)
        with Session(engine) as session:
            assert _presence(session, listed) == (1, 1)
            assert _presence(session, drifted) == (1, 0)
    finally:
        engine.dispose()


def test_old_writes_are_visible_to_new_readers(migration_db: str) -> None:
    """The old invite/provisioning transaction writes membership and assignments."""
    engine = _engine(migration_db)
    try:
        with engine.begin() as conn:
            org_id, role_id = _seed_org(conn)
            user_id = _seed_user(conn)
            ws_id = _seed_workspace(conn, org_id)
            conn.execute(
                text("INSERT INTO membership VALUES (:u, :w)"),
                {"u": user_id, "w": ws_id},
            )
            conn.execute(
                text(
                    "INSERT INTO organization_membership (user_id, organization_id) VALUES (:u, :o)"
                ),
                {"u": user_id, "o": org_id},
            )
            _assign(conn, org_id, user_id, ws_id, role_id)
            _assign(conn, org_id, user_id, None, role_id)
        with Session(engine) as session:
            assert _presence(session, user_id) == (1, 1)
            assert session.scalar(select(LegacyMembership.user_id)) == user_id
            assert (
                session.scalar(select(LegacyOrganizationMembership.user_id)) == user_id
            )
        # Dropping only the compatibility rows cannot change new-reader access.
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM membership WHERE user_id = :u"), {"u": user_id}
            )
            conn.execute(
                text("DELETE FROM organization_membership WHERE user_id = :u"),
                {"u": user_id},
            )
        with Session(engine) as session:
            assert _presence(session, user_id) == (1, 1)
    finally:
        engine.dispose()


def test_repeat_backfill_keeps_revocations_and_covers_late_writes(
    migration_db: str,
) -> None:
    """Rollback and another backfill cannot resurrect a dual-written removal."""
    engine = _engine(migration_db)
    try:
        with engine.begin() as conn:
            org_id, editor_role = _seed_org(conn)
            revoked, late = _seed_user(conn), _seed_user(conn)
            ws_id = _seed_workspace(conn, org_id)
            for user_id in (revoked, late):
                conn.execute(
                    text("INSERT INTO membership VALUES (:u, :w)"),
                    {"u": user_id, "w": ws_id},
                )
                conn.execute(
                    text(
                        "INSERT INTO organization_membership (user_id, organization_id) "
                        "VALUES (:u, :o)"
                    ),
                    {"u": user_id, "o": org_id},
                )
            _assign(conn, org_id, revoked, ws_id, editor_role)
            _assign(conn, org_id, revoked, None, editor_role)
            # The bridge removes assignments and legacy membership together.
            for table in (
                "user_role_assignment",
                "membership",
                "organization_membership",
            ):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": revoked}
                )

        # Simulate returning to the old schema, then the final backfill pass.
        _run_alembic(migration_db, "downgrade", PREVIOUS_REVISION)
        with Session(engine) as session:
            assert _presence(session, revoked) == (0, 0)
            assert _presence(session, late) == (0, 0)
            assert (
                session.scalar(
                    select(LegacyMembership.user_id).where(
                        LegacyMembership.user_id == late
                    )
                )
                == late
            )
        _run_alembic(migration_db, "upgrade", MIGRATION_REVISION)
        with Session(engine) as session:
            assert _presence(session, revoked) == (0, 0)
            assert _presence(session, late) == (1, 1)
            assert (
                session.execute(
                    text(
                        "SELECT count(*) FROM user_role_assignment WHERE user_id = :u"
                    ),
                    {"u": revoked},
                ).scalar_one()
                == 0
            )
            assert (
                session.execute(
                    text(
                        "SELECT count(*) FROM user_role_assignment WHERE user_id = :u"
                    ),
                    {"u": late},
                ).scalar_one()
                == 2
            )
    finally:
        engine.dispose()


def test_org_membership_writer_respects_tenant_rls(migration_db: str) -> None:
    """Org writers can mirror their own workspaces, never another tenant's."""
    engine = _engine(migration_db)
    try:
        with engine.connect() as conn:
            org_id, _ = _seed_org(conn)
            other_org, _ = _seed_org(conn)
            user_id = _seed_user(conn)
            workspace_id = _seed_workspace(conn, org_id)
            other_workspace = _seed_workspace(conn, other_org)
            role_name = f"membership_writer_{uuid.uuid4().hex}"
            # This test-only role and its grants disappear on transaction rollback.
            conn.execute(text(f'CREATE ROLE "{role_name}" NOLOGIN'))
            conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role_name}"'))
            conn.execute(text(f'GRANT SELECT ON workspace TO "{role_name}"'))
            conn.execute(
                text(f'GRANT SELECT, INSERT, DELETE ON membership TO "{role_name}"')
            )
            conn.execute(text(f'SET LOCAL ROLE "{role_name}"'))
            conn.execute(
                text(
                    "SELECT set_config('app.current_org_id', :org, true), "
                    "set_config('app.current_workspace_id', '', true), "
                    "set_config('app.rls_bypass', 'off', true)"
                ),
                {"org": str(org_id)},
            )
            statement = text("INSERT INTO membership VALUES (:u, :w)")
            conn.execute(statement, {"u": user_id, "w": workspace_id})
            assert conn.execute(
                text("SELECT workspace_id FROM membership")
            ).scalars().all() == [workspace_id]
            with conn.begin_nested() as savepoint:
                with pytest.raises(ProgrammingError):
                    conn.execute(statement, {"u": user_id, "w": other_workspace})
                savepoint.rollback()
            conn.execute(text("DELETE FROM membership"))
            assert (
                conn.execute(text("SELECT count(*) FROM membership")).scalar_one() == 0
            )
            conn.rollback()
    finally:
        engine.dispose()
