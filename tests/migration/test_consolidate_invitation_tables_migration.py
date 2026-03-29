"""Tests for invitation table consolidation migration."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "2e17d6f6f0d5"
PREVIOUS_REVISION = "0a1e3100a432"


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
            "Alembic command failed:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


@pytest.fixture(scope="function")
def migration_db_url() -> Iterator[str]:
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    db_name = f"test_invitation_consolidation_{uuid.uuid4().hex[:8]}"
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


def _insert_rls_context(
    conn,
    *,
    org_id: uuid.UUID | None,
    workspace_id: uuid.UUID | None,
    bypass: bool = False,
) -> None:
    conn.execute(
        text(
            """
            SELECT
                set_config('app.rls_bypass', :bypass, true),
                set_config('app.current_org_id', :org_id, true),
                set_config('app.current_workspace_id', :workspace_id, true)
            """
        ),
        {
            "bypass": "on" if bypass else "off",
            "org_id": str(org_id) if org_id else "",
            "workspace_id": str(workspace_id) if workspace_id else "",
        },
    )


def _seed_pre_migration_invitations(db_url: str) -> dict[str, uuid.UUID]:
    engine = create_engine(db_url, poolclass=NullPool)
    ids = {
        "org_a": uuid.uuid4(),
        "org_b": uuid.uuid4(),
        "workspace_a": uuid.uuid4(),
        "workspace_b": uuid.uuid4(),
        "org_role_a": uuid.uuid4(),
        "workspace_role_a": uuid.uuid4(),
        "workspace_role_b": uuid.uuid4(),
        "workspace_invite_a": uuid.uuid4(),
        "workspace_invite_b": uuid.uuid4(),
        "org_invite_a": uuid.uuid4(),
    }

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES
                        (:org_a, 'Org A', 'org-a', true),
                        (:org_b, 'Org B', 'org-b', true)
                    """
                ),
                {"org_a": ids["org_a"], "org_b": ids["org_b"]},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES
                        (:workspace_a, :org_a, 'Workspace A'),
                        (:workspace_b, :org_b, 'Workspace B')
                    """
                ),
                {
                    "workspace_a": ids["workspace_a"],
                    "workspace_b": ids["workspace_b"],
                    "org_a": ids["org_a"],
                    "org_b": ids["org_b"],
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO role (id, name, slug, description, organization_id)
                    VALUES
                        (
                            :org_role_a,
                            'Organization Member',
                            'organization-member',
                            'Org member role',
                            :org_a
                        ),
                        (
                            :workspace_role_a,
                            'Workspace Editor',
                            'workspace-editor',
                            'Workspace editor role',
                            :org_a
                        ),
                        (
                            :workspace_role_b,
                            'Workspace Editor',
                            'workspace-editor',
                            'Workspace editor role',
                            :org_b
                        )
                    """
                ),
                {
                    "org_role_a": ids["org_role_a"],
                    "workspace_role_a": ids["workspace_role_a"],
                    "workspace_role_b": ids["workspace_role_b"],
                    "org_a": ids["org_a"],
                    "org_b": ids["org_b"],
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO invitation (
                        id,
                        workspace_id,
                        email,
                        status,
                        invited_by,
                        role_id,
                        token,
                        expires_at,
                        accepted_at
                    )
                    VALUES
                        (
                            :workspace_invite_a,
                            :workspace_a,
                            'shared@example.com',
                            'PENDING',
                            NULL,
                            :workspace_role_a,
                            :workspace_token_a,
                            now() + interval '7 days',
                            NULL
                        ),
                        (
                            :workspace_invite_b,
                            :workspace_b,
                            'other@example.com',
                            'PENDING',
                            NULL,
                            :workspace_role_b,
                            :workspace_token_b,
                            now() + interval '7 days',
                            NULL
                        )
                    """
                ),
                {
                    "workspace_invite_a": ids["workspace_invite_a"],
                    "workspace_invite_b": ids["workspace_invite_b"],
                    "workspace_a": ids["workspace_a"],
                    "workspace_b": ids["workspace_b"],
                    "workspace_role_a": ids["workspace_role_a"],
                    "workspace_role_b": ids["workspace_role_b"],
                    "workspace_token_a": uuid.uuid4().hex + uuid.uuid4().hex,
                    "workspace_token_b": uuid.uuid4().hex + uuid.uuid4().hex,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO organization_invitation (
                        id,
                        organization_id,
                        email,
                        status,
                        invited_by,
                        role_id,
                        token,
                        expires_at,
                        accepted_at
                    )
                    VALUES (
                        :org_invite_a,
                        :org_a,
                        'shared@example.com',
                        'PENDING',
                        NULL,
                        :org_role_a,
                        :org_token_a,
                        now() + interval '7 days',
                        NULL
                    )
                    """
                ),
                {
                    "org_invite_a": ids["org_invite_a"],
                    "org_a": ids["org_a"],
                    "org_role_a": ids["org_role_a"],
                    "org_token_a": uuid.uuid4().hex + uuid.uuid4().hex,
                },
            )
    finally:
        engine.dispose()

    return ids


def _get_invitation_policy(engine: Engine) -> tuple[str, str]:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT qual, with_check
                FROM pg_policies
                WHERE schemaname = 'public'
                  AND tablename = 'invitation'
                  AND policyname = 'rls_policy_invitation'
                """
            )
        ).one()
    return row[0], row[1]


def test_upgrade_swaps_invitation_policy_to_org_optional_workspace(
    migration_db_url: str,
) -> None:
    _seed_pre_migration_invitations(migration_db_url)
    _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        qual, with_check = _get_invitation_policy(engine)
        qual_lower = qual.lower()
        with_check_lower = with_check.lower()

        assert "organization_id" in qual_lower
        assert "workspace_id is null" in qual_lower
        assert "app.current_org_id" in qual_lower
        assert "organization_id" in with_check_lower
        assert "workspace_id is null" in with_check_lower
    finally:
        engine.dispose()


def test_upgrade_policy_allows_org_context_without_workspace(
    migration_db_url: str,
) -> None:
    ids = _seed_pre_migration_invitations(migration_db_url)
    _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

    engine = create_engine(migration_db_url, poolclass=NullPool)
    rls_role_name = f"rls_invitation_reader_{uuid.uuid4().hex[:8]}"
    try:
        with engine.begin() as conn:
            conn.execute(text('ALTER TABLE "invitation" FORCE ROW LEVEL SECURITY'))
            conn.execute(text(f'CREATE ROLE "{rls_role_name}" NOLOGIN'))
            conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{rls_role_name}"'))
            conn.execute(text(f'GRANT SELECT ON "invitation" TO "{rls_role_name}"'))
            try:
                conn.execute(text(f'SET ROLE "{rls_role_name}"'))

                _insert_rls_context(conn, org_id=ids["org_a"], workspace_id=None)
                org_a_rows = conn.execute(
                    text(
                        """
                        SELECT email, workspace_id
                        FROM invitation
                        ORDER BY email, workspace_id NULLS FIRST
                        """
                    )
                ).all()
                assert org_a_rows == [
                    ("shared@example.com", None),
                    ("shared@example.com", ids["workspace_a"]),
                ]

                _insert_rls_context(conn, org_id=ids["org_b"], workspace_id=None)
                org_b_rows = conn.execute(
                    text(
                        """
                        SELECT email, workspace_id
                        FROM invitation
                        ORDER BY email, workspace_id NULLS FIRST
                        """
                    )
                ).all()
                assert org_b_rows == [("other@example.com", ids["workspace_b"])]
            finally:
                conn.execute(text("RESET ROLE"))
                conn.execute(
                    text(f'REVOKE SELECT ON "invitation" FROM "{rls_role_name}"')
                )
                conn.execute(
                    text(f'REVOKE USAGE ON SCHEMA public FROM "{rls_role_name}"')
                )
                conn.execute(text(f'DROP ROLE "{rls_role_name}"'))
    finally:
        engine.dispose()


def test_downgrade_restores_workspace_only_invitation_policy(
    migration_db_url: str,
) -> None:
    _seed_pre_migration_invitations(migration_db_url)
    _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)
    _run_alembic(migration_db_url, "downgrade", PREVIOUS_REVISION)

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        qual, with_check = _get_invitation_policy(engine)
        qual_lower = qual.lower()
        with_check_lower = with_check.lower()

        assert "app.current_workspace_id" in qual_lower
        assert "organization_id" not in qual_lower
        assert "workspace_id is null" not in qual_lower
        assert "app.current_workspace_id" in with_check_lower
        assert "organization_id" not in with_check_lower
    finally:
        engine.dispose()
