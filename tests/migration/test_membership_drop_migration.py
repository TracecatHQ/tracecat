"""Tests for the revision that drops the legacy membership tables.

Revision 6a6e9e93f9ef re-runs the membership backfill and guard, then drops
`membership` and `organization_membership`. Downgrade recreates them from the
assignment graph.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, select, text
from sqlalchemy.orm import Session

from tests.database import TEST_DB_CONFIG
from tracecat.db.models import Membership, OrganizationMembership

MIGRATION_REVISION = "6a6e9e93f9ef"
PREVIOUS_REVISION = "4134d4ebdc69"
LEGACY_TABLES = ("membership", "organization_membership")


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
    return conn.execute(
        text(
            "SELECT relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = :name"
        ),
        {"name": name},
    ).scalar_one_or_none()


def _policy_count(conn: Connection, table: str) -> int:
    return conn.execute(
        text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}
    ).scalar_one()


@pytest.fixture(scope="function")
def previous_db() -> Iterator[str]:
    """An empty database at the revision before the one under test."""
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT"
    )
    db_name = f"test_membership_drop_{uuid.uuid4().hex[:8]}"
    try:
        with default_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        url = TEST_DB_CONFIG.test_url_sync.replace(TEST_DB_CONFIG.test_db_name, db_name)
        _run_alembic(url, "upgrade", PREVIOUS_REVISION)
        yield url
    finally:
        with default_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        default_engine.dispose()


def _engine(url: str) -> Engine:
    return create_engine(url)


def _seed_org(conn: Connection) -> tuple[uuid.UUID, uuid.UUID]:
    """One org with the two system roles the backfill needs."""
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


def _presence(session: Session, user_id: uuid.UUID) -> tuple[int, int]:
    workspaces = session.scalars(
        select(Membership).where(Membership.user_id == user_id)
    ).all()
    orgs = session.scalars(
        select(OrganizationMembership).where(OrganizationMembership.user_id == user_id)
    ).all()
    return len(workspaces), len(orgs)


def _legacy_rows(
    conn: Connection,
) -> tuple[set[tuple[uuid.UUID, uuid.UUID]], set[tuple[uuid.UUID, uuid.UUID]]]:
    ws = {
        (row.user_id, row.workspace_id)
        for row in conn.execute(text("SELECT user_id, workspace_id FROM membership"))
    }
    org = {
        (row.user_id, row.organization_id)
        for row in conn.execute(
            text("SELECT user_id, organization_id FROM organization_membership")
        )
    }
    return ws, org


def test_upgrade_covers_late_rows_then_drops_and_downgrade_restores_a_superset(
    previous_db: str,
) -> None:
    """Rows written by old pods still get an assignment; downgrade loses nothing."""
    engine = _engine(previous_db)
    try:
        with engine.begin() as conn:
            org_id, editor_role = _seed_org(conn)
            late, drifted = _seed_user(conn), _seed_user(conn)
            ws_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO workspace (id, name, organization_id) "
                    "VALUES (:id, 'ws', :org)"
                ),
                {"id": ws_id, "org": org_id},
            )
            # `late`: written to the legacy tables by an old pod, no assignment.
            conn.execute(
                text("INSERT INTO membership (user_id, workspace_id) VALUES (:u, :w)"),
                {"u": late, "w": ws_id},
            )
            conn.execute(
                text(
                    "INSERT INTO organization_membership (user_id, organization_id) "
                    "VALUES (:u, :o)"
                ),
                {"u": late, "o": org_id},
            )
            # `drifted`: assignment only, never listed.
            conn.execute(
                text(
                    "INSERT INTO user_role_assignment "
                    "(id, organization_id, user_id, workspace_id, role_id) "
                    "VALUES (gen_random_uuid(), :org, :u, :ws, :role)"
                ),
                {"org": org_id, "u": drifted, "ws": ws_id, "role": editor_role},
            )
            before_ws, before_org = _legacy_rows(conn)

        _run_alembic(previous_db, "upgrade", MIGRATION_REVISION)
        with engine.connect() as conn:
            for name in LEGACY_TABLES:
                assert _relkind(conn, name) is None, f"{name} should be dropped"
        with Session(engine) as session:
            assert _presence(session, late) == (1, 1)
            assert _presence(session, drifted) == (1, 0)

        _run_alembic(previous_db, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            for name in LEGACY_TABLES:
                assert _relkind(conn, name) == "r", f"{name} should be recreated"
                assert _policy_count(conn, name) > 0, f"{name} should have RLS again"
            after_ws, after_org = _legacy_rows(conn)
            assert before_ws <= after_ws and before_org <= after_org
            assert (drifted, ws_id) in after_ws
    finally:
        engine.dispose()
