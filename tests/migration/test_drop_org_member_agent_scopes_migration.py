"""Tests for dropping agent scopes from the organization-member role."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "a3f8c2d1e9b4"
PREVIOUS_REVISION = "3b8f2c1d9e7a"


def _run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
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
            "Alembic command failed:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


@pytest.fixture(scope="function")
def migration_db_url() -> Iterator[str]:
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    db_name = f"test_member_agent_{uuid.uuid4().hex[:8]}"
    termination_query = text(
        f"""
        SELECT pg_terminate_backend(pg_stat_activity.pid)
        FROM pg_stat_activity
        WHERE pg_stat_activity.datname = '{db_name}'
          AND pid <> pg_backend_pid();
        """
    )

    try:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))

        db_url = TEST_DB_CONFIG.test_url_sync.replace(
            TEST_DB_CONFIG.test_db_name, db_name
        )
        _run_alembic(db_url, "upgrade", PREVIOUS_REVISION)
        yield db_url
    finally:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        default_engine.dispose()


def _role_scopes(engine: Engine) -> set[tuple[str, str, bool]]:
    """Return (role slug, scope name, platform-owned) for every link."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT role.slug, scope.name, scope.organization_id IS NULL
                FROM role_scope
                JOIN role ON role.id = role_scope.role_id
                JOIN scope ON scope.id = role_scope.scope_id
                """
            )
        ).all()
    return {(row[0], row[1], row[2]) for row in rows}


def test_migration_drops_member_agent_links_and_downgrade_restores_them(
    migration_db_url: str,
) -> None:
    """Upgrade unlinks only the member role's platform agent scopes; downgrade relinks them.

    Legacy assignments of organization-member still read the role's links, so
    agent:read and agent:execute must leave that role. Other roles, other
    scopes, and an org-defined scope that shares a name stay untouched.
    """
    org_id = uuid.uuid4()
    member_role_id = uuid.uuid4()
    viewer_role_id = uuid.uuid4()
    platform_scope_ids = {
        name: uuid.uuid4() for name in ("agent:read", "agent:execute", "org:read")
    }
    custom_scope_id = uuid.uuid4()

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:org_id, 'Migration org', :org_slug, true)
                    """
                ),
                {"org_id": org_id, "org_slug": f"migration-{org_id.hex[:8]}"},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO role (id, organization_id, name, slug)
                    VALUES
                        (:member_role_id, :org_id, 'Organization Member', 'organization-member'),
                        (:viewer_role_id, :org_id, 'Workspace Viewer', 'workspace-viewer')
                    """
                ),
                {
                    "member_role_id": member_role_id,
                    "viewer_role_id": viewer_role_id,
                    "org_id": org_id,
                },
            )
            for name, scope_id in platform_scope_ids.items():
                resource, action = name.split(":")
                conn.execute(
                    text(
                        """
                        INSERT INTO scope (id, name, resource, action, source, organization_id)
                        VALUES (:id, :name, :resource, :action, 'PLATFORM', NULL)
                        """
                    ),
                    {
                        "id": scope_id,
                        "name": name,
                        "resource": resource,
                        "action": action,
                    },
                )
            conn.execute(
                text(
                    """
                    INSERT INTO scope (id, name, resource, action, source, organization_id)
                    VALUES (:id, 'agent:execute', 'agent', 'execute', 'CUSTOM', :org_id)
                    """
                ),
                {"id": custom_scope_id, "org_id": org_id},
            )
            links = [
                (member_role_id, platform_scope_ids["agent:read"]),
                (member_role_id, platform_scope_ids["agent:execute"]),
                (member_role_id, platform_scope_ids["org:read"]),
                (member_role_id, custom_scope_id),
                (viewer_role_id, platform_scope_ids["agent:read"]),
            ]
            for role_id, scope_id in links:
                conn.execute(
                    text(
                        "INSERT INTO role_scope (role_id, scope_id) VALUES (:role_id, :scope_id)"
                    ),
                    {"role_id": role_id, "scope_id": scope_id},
                )

        before = _role_scopes(engine)

        _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)
        assert _role_scopes(engine) == {
            ("organization-member", "org:read", True),
            ("organization-member", "agent:execute", False),
            ("workspace-viewer", "agent:read", True),
        }

        _run_alembic(migration_db_url, "downgrade", PREVIOUS_REVISION)
        assert _role_scopes(engine) == before
    finally:
        engine.dispose()
