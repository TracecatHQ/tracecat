"""Invitation rollback preserves direct membership for legacy application readers."""

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

PREVIOUS_REVISION = "526f867f6a75"
INVITATION_REVISION = "e847d14eeb86"


@pytest.fixture(scope="session")
def default_org() -> None:
    """Use an isolated migration database instead of shared application fixtures."""


@pytest.fixture(scope="session")
def workflow_bucket() -> None:
    """No object storage is needed."""


@pytest.fixture
def clean_redis_db() -> None:
    """No Redis is needed."""


def _migrate(db_url: str, direction: str, revision: str) -> None:
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = db_url
    result = subprocess.run(
        ["uv", "run", "alembic", direction, revision],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def migration_db_url() -> Iterator[str]:
    db_name = f"test_invitation_rollback_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(
        TEST_DB_CONFIG.sys_url_sync,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    db_url = TEST_DB_CONFIG.base_url.replace("+asyncpg", "+psycopg") + db_name
    with admin_engine.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))
    try:
        _migrate(db_url, "upgrade", PREVIOUS_REVISION)
        yield db_url
    finally:
        with admin_engine.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE "{db_name}"'))
        admin_engine.dispose()


def test_downgrade_restores_current_direct_memberships(
    migration_db_url: str,
) -> None:
    _migrate(migration_db_url, "upgrade", INVITATION_REVISION)
    engine = sa.create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("""
                INSERT INTO organization (id, name, slug, is_active)
                VALUES (md5('rollback-org')::uuid, 'Rollback test', 'rollback-test', true);
                INSERT INTO workspace (id, organization_id, name)
                VALUES (md5('rollback-workspace')::uuid, md5('rollback-org')::uuid,
                        'Rollback workspace');
                INSERT INTO "user" (id, email, hashed_password, is_active,
                                    is_superuser, is_verified, role)
                SELECT md5(label)::uuid, label || '@example.com', 'unused',
                       true, false, true, 'BASIC'
                FROM unnest(ARRAY['org-only', 'workspace-only', 'both',
                                  'revoked', 'existing', 'group-only']) AS label;
                INSERT INTO role (id, organization_id, name, slug)
                VALUES (md5('rollback-role')::uuid, md5('rollback-org')::uuid,
                        'Rollback role', 'rollback-role');
                INSERT INTO user_role_assignment
                    (id, organization_id, user_id, workspace_id, role_id)
                SELECT gen_random_uuid(), md5('rollback-org')::uuid,
                       md5(label)::uuid, NULL, md5('rollback-role')::uuid
                FROM unnest(ARRAY['org-only', 'both', 'existing', 'revoked']) AS label;
                INSERT INTO user_role_assignment
                    (id, organization_id, user_id, workspace_id, role_id)
                SELECT gen_random_uuid(), md5('rollback-org')::uuid,
                       md5(label)::uuid, md5('rollback-workspace')::uuid,
                       md5('rollback-role')::uuid
                FROM unnest(ARRAY['workspace-only', 'both', 'existing', 'revoked']) AS label;
                INSERT INTO organization_membership (user_id, organization_id)
                VALUES (md5('existing')::uuid, md5('rollback-org')::uuid);
                INSERT INTO membership (user_id, workspace_id)
                VALUES (md5('existing')::uuid, md5('rollback-workspace')::uuid);
                INSERT INTO "group" (id, organization_id, name)
                VALUES (md5('rollback-group')::uuid, md5('rollback-org')::uuid,
                        'Rollback group');
                INSERT INTO group_member (user_id, group_id)
                VALUES (md5('group-only')::uuid, md5('rollback-group')::uuid);
                INSERT INTO group_role_assignment
                    (id, organization_id, group_id, workspace_id, role_id)
                SELECT gen_random_uuid(), md5('rollback-org')::uuid,
                       md5('rollback-group')::uuid, workspace_id,
                       md5('rollback-role')::uuid
                FROM (VALUES (NULL::uuid), (md5('rollback-workspace')::uuid))
                     AS scopes(workspace_id);
                INSERT INTO invitation
                    (id, organization_id, email, status, token, expires_at,
                     accepted_at, created_by_platform_admin)
                VALUES (md5('revoked-invite')::uuid, md5('rollback-org')::uuid,
                        'revoked@example.com', 'ACCEPTED', 'rollback-test-token',
                        now() + interval '1 day', now(), false);
                INSERT INTO invitation_grant
                    (id, organization_id, invitation_id, workspace_id, role_id)
                VALUES (gen_random_uuid(), md5('rollback-org')::uuid,
                        md5('revoked-invite')::uuid, NULL, md5('rollback-role')::uuid);
                DELETE FROM user_role_assignment WHERE user_id = md5('revoked')::uuid;
            """)
            )
            assert conn.scalar(sa.text("SELECT count(*) FROM membership")) == 1
            assert (
                conn.scalar(sa.text("SELECT count(*) FROM organization_membership"))
                == 1
            )
            assignments_before = conn.execute(
                sa.text(
                    "SELECT id, user_id, workspace_id, role_id FROM user_role_assignment ORDER BY id"
                )
            ).all()

        # A second cycle exercises re-upgrade compatibility and conflict handling.
        for _ in range(2):
            _migrate(migration_db_url, "downgrade", PREVIOUS_REVISION)
            with engine.connect() as conn:
                assert conn.execute(
                    sa.text("""
                    SELECT u.email FROM organization_membership m
                    JOIN "user" u ON u.id = m.user_id ORDER BY u.email
                """)
                ).scalars().all() == [
                    "both@example.com",
                    "existing@example.com",
                    "org-only@example.com",
                ]
                assert conn.execute(
                    sa.text("""
                    SELECT u.email FROM membership m
                    JOIN "user" u ON u.id = m.user_id ORDER BY u.email
                """)
                ).scalars().all() == [
                    "both@example.com",
                    "existing@example.com",
                    "workspace-only@example.com",
                ]
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT id, user_id, workspace_id, role_id FROM user_role_assignment ORDER BY id"
                        )
                    ).all()
                    == assignments_before
                )
                assert (
                    conn.scalar(sa.text("SELECT count(*) FROM group_role_assignment"))
                    == 2
                )
            _migrate(migration_db_url, "upgrade", INVITATION_REVISION)
    finally:
        engine.dispose()
