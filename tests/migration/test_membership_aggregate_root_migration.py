"""Tests for the backfill that makes organization membership the aggregate root."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "fa922accd060"
PREVIOUS_REVISION = "a7c3e9f1b2d4"


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
    db_name = f"test_membership_aggregate_{uuid.uuid4().hex[:8]}"
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
            TEST_DB_CONFIG.test_db_name,
            db_name,
        )
        _run_alembic(db_url, "upgrade", PREVIOUS_REVISION)
        yield db_url
    finally:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        default_engine.dispose()


def _insert_org(conn: Connection) -> uuid.UUID:
    org_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO organization (id, name, slug, is_active)
            VALUES (:id, 'Migration org', :slug, true)
            """
        ),
        {"id": org_id, "slug": f"migration-{org_id.hex[:8]}"},
    )
    return org_id


def _insert_user(conn: Connection) -> uuid.UUID:
    user_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO "user" (id, email, hashed_password, is_active, is_superuser,
                                is_verified, role)
            VALUES (:id, :email, 'x', true, false, false, 'BASIC')
            """
        ),
        {"id": str(user_id), "email": f"migration-{user_id.hex[:8]}@example.com"},
    )
    return user_id


def _insert_role(conn: Connection, organization_id: uuid.UUID) -> uuid.UUID:
    role_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO role (id, name, slug, organization_id)
            VALUES (:id, :name, NULL, :organization_id)
            """
        ),
        {
            "id": role_id,
            "name": f"migration-{role_id.hex[:8]}",
            "organization_id": organization_id,
        },
    )
    return role_id


def _membership_exists(
    conn: Connection, *, organization_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    return (
        conn.execute(
            text(
                """
                SELECT 1 FROM organization_membership
                WHERE organization_id = :organization_id AND user_id = :user_id
                """
            ),
            {"organization_id": organization_id, "user_id": str(user_id)},
        ).scalar_one_or_none()
        is not None
    )


def test_orphan_assignment_gains_a_membership_row(migration_db_url: str) -> None:
    """An assignment with no membership row gets one so the new FK validates."""
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            org_id = _insert_org(conn)
            user_id = _insert_user(conn)
            role_id = _insert_role(conn, org_id)
            conn.execute(
                text(
                    """
                    INSERT INTO user_role_assignment
                        (id, organization_id, user_id, workspace_id, role_id)
                    VALUES (:id, :organization_id, :user_id, NULL, :role_id)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "organization_id": org_id,
                    "user_id": str(user_id),
                    "role_id": role_id,
                },
            )
            assert not _membership_exists(conn, organization_id=org_id, user_id=user_id)

        _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

        with engine.connect() as conn:
            assert _membership_exists(conn, organization_id=org_id, user_id=user_id)
    finally:
        engine.dispose()


def test_removed_service_account_owner_is_cleared_not_readmitted(
    migration_db_url: str,
) -> None:
    """Ownership metadata is not membership: a pathless owner is nulled out."""
    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            org_id = _insert_org(conn)
            owner_id = _insert_user(conn)
            service_account_id = uuid.uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO service_account
                        (id, organization_id, name, owner_user_id)
                    VALUES (:id, :organization_id, :name, :owner_user_id)
                    """
                ),
                {
                    "id": service_account_id,
                    "organization_id": org_id,
                    "name": f"sa-{service_account_id.hex[:8]}",
                    "owner_user_id": str(owner_id),
                },
            )

        _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

        with engine.connect() as conn:
            owner_user_id = conn.execute(
                text("SELECT owner_user_id FROM service_account WHERE id = :id"),
                {"id": service_account_id},
            ).scalar_one()
            assert owner_user_id is None
            assert not _membership_exists(
                conn, organization_id=org_id, user_id=owner_id
            )
    finally:
        engine.dispose()
