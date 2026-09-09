"""Tests for membership admission and rolling-version compatibility."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Connection, Engine, create_engine, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.api.app import app
from tracecat.auth.credentials import _compute_effective_scopes_cached
from tracecat.auth.users import current_active_user, optional_current_active_user
from tracecat.authz.enums import ScopeSource
from tracecat.db import engine as db_engine
from tracecat.db.models import (
    Group,
    GroupMember,
    GroupRoleAssignment,
    LegacyMembership,
    LegacyOrganizationMembership,
    Membership,
    OrganizationMembership,
    Role,
    Scope,
    User,
    UserRoleAssignment,
)

MIGRATION_REVISION = "4134d4ebdc69"
PREVIOUS_REVISION = "526f867f6a75"
# Columns retained for both deployed and rollback readers.
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
    """One org with representative system roles.

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
    """Membership visible through the read-only compatibility mappings."""
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


def test_upgrade_preserves_legacy_admission_without_repair(previous_db: str) -> None:
    """Unprivileged membership stays unprivileged; assignment-only users stay blocked."""
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
            assert _presence(session, drifted) == (0, 0)
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
        # An old writer revoking membership must also revoke new-reader admission.
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM membership WHERE user_id = :u"), {"u": user_id}
            )
            conn.execute(
                text("DELETE FROM organization_membership WHERE user_id = :u"),
                {"u": user_id},
            )
        with Session(engine) as session:
            assert _presence(session, user_id) == (0, 0)
    finally:
        engine.dispose()


def test_repeat_upgrade_preserves_revocations_and_late_memberships(
    migration_db: str,
) -> None:
    """Rollback and repeated upgrades preserve both revocations and late writes."""
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

        # Simulate application rollback and rerunning the additive migration.
        _run_alembic(migration_db, "downgrade", PREVIOUS_REVISION)
        with Session(engine) as session:
            assert _presence(session, revoked) == (0, 0)
            assert _presence(session, late) == (1, 1)
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
                == 0
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


@dataclass(frozen=True, slots=True)
class AccessCase:
    name: str
    workspace_member: bool
    org_member: bool
    workspace_role: bool = False
    org_role: bool = False
    group_role: bool = False
    expected_status: int = 200
    expected_scopes: tuple[str, ...] = ()


@pytest.mark.anyio
async def test_upgrade_preserves_access_without_adding_permissions(
    previous_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise real auth and scope resolution against both legacy and new rows.

    Only login identity and the database connection are substituted. The cases
    cover the old membership gate independently of direct/group permission paths.
    """
    cases = (
        AccessCase(
            "direct",
            True,
            True,
            workspace_role=True,
            expected_scopes=("workspace:read",),
        ),
        AccessCase(
            "group", True, True, group_role=True, expected_scopes=("workspace:read",)
        ),
        AccessCase(
            "inherited", True, True, org_role=True, expected_scopes=("workspace:read",)
        ),
        AccessCase("membership_without_permissions", True, True),
        AccessCase(
            "blocked_direct", False, True, workspace_role=True, expected_status=403
        ),
        AccessCase("blocked_group", False, True, group_role=True, expected_status=403),
        AccessCase("blocked_org_role", False, True, org_role=True, expected_status=403),
        AccessCase(
            "workspace_without_org_membership",
            True,
            False,
            workspace_role=True,
            expected_scopes=("workspace:read",),
        ),
    )
    engine = _engine(previous_db)
    accounts: list[tuple[AccessCase, User]] = []
    try:
        with engine.begin() as conn:
            org_id, editor_id = _seed_org(conn)
            workspace_id = _seed_workspace(conn, org_id)
        with Session(engine, expire_on_commit=False) as session:
            read_scope = Scope(
                name="workspace:read",
                resource="workspace",
                action="read",
                source=ScopeSource.PLATFORM,
            )
            update_scope = Scope(
                name="workspace:update",
                resource="workspace",
                action="update",
                source=ScopeSource.PLATFORM,
            )
            reader = Role(name="Reader", organization_id=org_id, scopes=[read_scope])
            editor = session.get(Role, editor_id)
            assert editor is not None
            editor.scopes = [read_scope, update_scope]
            session.add(reader)
            session.flush()
            for case in cases:
                user = User(
                    email=f"{case.name}@example.com",
                    hashed_password="synthetic",
                    is_active=True,
                    is_verified=True,
                    is_superuser=False,
                )
                session.add(user)
                session.flush()
                accounts.append((case, user))
                if case.workspace_member:
                    session.add(
                        LegacyMembership(user_id=user.id, workspace_id=workspace_id)
                    )
                if case.org_member:
                    session.add(
                        LegacyOrganizationMembership(
                            user_id=user.id, organization_id=org_id
                        )
                    )
                if case.workspace_role or case.org_role:
                    session.add(
                        UserRoleAssignment(
                            user_id=user.id,
                            organization_id=org_id,
                            workspace_id=None if case.org_role else workspace_id,
                            role_id=reader.id,
                        )
                    )
                if case.group_role:
                    group = Group(name=case.name, organization_id=org_id)
                    session.add(group)
                    session.flush()
                    session.add(GroupMember(user_id=user.id, group_id=group.id))
                    session.add(
                        GroupRoleAssignment(
                            group_id=group.id,
                            organization_id=org_id,
                            workspace_id=workspace_id,
                            role_id=reader.id,
                        )
                    )
            session.commit()
            before_assignments = set(
                session.execute(
                    select(UserRoleAssignment.id, UserRoleAssignment.role_id)
                ).tuples()
            )
        _run_alembic(previous_db, "upgrade", MIGRATION_REVISION)
        with Session(engine) as session:
            after_assignments = set(
                session.execute(
                    select(UserRoleAssignment.id, UserRoleAssignment.role_id)
                ).tuples()
            )
            assert after_assignments == before_assignments, (
                "An expand migration must not assign default permissions"
            )

        async_engine = create_async_engine(previous_db.replace("+psycopg", "+asyncpg"))
        monkeypatch.setattr(db_engine, "_async_engine", async_engine)
        monkeypatch.setattr(db_engine, "_async_auth_engine", async_engine)
        monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
        previous_overrides = app.dependency_overrides.copy()
        actor = accounts[0][1]

        async def logged_in_user() -> User:
            return actor

        app.dependency_overrides[current_active_user] = logged_in_user
        app.dependency_overrides[optional_current_active_user] = logged_in_user
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test.local"
            ) as client:
                for case, account in accounts:
                    actor = account
                    _compute_effective_scopes_cached.cache_clear()
                    response = await client.get(
                        "/users/me/scopes", params={"workspace_id": str(workspace_id)}
                    )
                    assert response.status_code == case.expected_status, (
                        case.name,
                        response.text,
                    )
                    if case.expected_status == 200:
                        assert response.json()["scopes"] == list(
                            case.expected_scopes
                        ), case.name

                    org_response = await client.get("/users/me/scopes")
                    assert org_response.status_code == (
                        200 if case.org_member else 400
                    ), case.name
                    if case.org_member:
                        assert org_response.json()["scopes"] == (
                            ["workspace:read"] if case.org_role else []
                        ), case.name

                # An old pod can delete only an assignment while leaving membership.
                inherited = next(
                    user for case, user in accounts if case.name == "inherited"
                )
                with engine.begin() as conn:
                    _assign(conn, org_id, inherited.id, workspace_id, editor_id)
                actor = inherited
                _compute_effective_scopes_cached.cache_clear()
                response = await client.get(
                    "/users/me/scopes", params={"workspace_id": str(workspace_id)}
                )
                assert response.json()["scopes"] == [
                    "workspace:read",
                    "workspace:update",
                ]
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "DELETE FROM user_role_assignment WHERE user_id = :u AND workspace_id = :w"
                        ),
                        {"u": actor.id, "w": workspace_id},
                    )
                _compute_effective_scopes_cached.cache_clear()
                response = await client.get(
                    "/users/me/scopes", params={"workspace_id": str(workspace_id)}
                )
                assert response.status_code == 200
                assert response.json()["scopes"] == ["workspace:read"]
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous_overrides)
            _compute_effective_scopes_cached.cache_clear()
            await async_engine.dispose()
    finally:
        engine.dispose()
