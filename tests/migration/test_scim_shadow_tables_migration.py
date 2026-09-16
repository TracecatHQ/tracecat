"""Tests for the additive SCIM shadow-table migration."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "a667d946cca1"
PREVIOUS_REVISION = "e847d14eeb86"
NEW_TABLES = (
    "external_user",
    "external_group",
    "external_group_member",
    "external_group_mapping",
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
    for url in _fresh_database("test_scim_previous"):
        _run_alembic(url, "upgrade", PREVIOUS_REVISION)
        yield url


@pytest.fixture(scope="function")
def migration_db() -> Iterator[str]:
    """An empty database migrated up to the revision under test."""
    for url in _fresh_database("test_scim_shadow"):
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


def _column_exists(conn: Connection, table: str, column: str) -> bool:
    return (
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
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


def _seed_user(conn: Connection) -> uuid.UUID:
    """Insert a user with the columns every revision under test has."""
    user_id = uuid.uuid4()
    user_role = conn.execute(
        text(
            "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'userrole' LIMIT 1"
        )
    ).scalar_one()
    params: dict[str, object] = {
        "id": user_id,
        "email": f"{user_id.hex[:8]}@example.com",
        "role": user_role,
    }
    columns = "id, email, hashed_password, is_active, is_superuser, is_verified, role"
    values = ":id, :email, 'x', true, false, true, :role"
    conn.execute(
        text(f'INSERT INTO "user" ({columns}) VALUES ({values})'),
        params,
    )
    return user_id


def _seed_group(conn: Connection, org_id: uuid.UUID) -> uuid.UUID:
    group_id = uuid.uuid4()
    conn.execute(
        text(
            'INSERT INTO "group" (id, name, organization_id) VALUES (:id, :name, :org)'
        ),
        {"id": group_id, "name": f"g-{group_id.hex[:8]}", "org": org_id},
    )
    return group_id


def test_upgrade_creates_shadow_tables(migration_db: str) -> None:
    """The four shadow tables land together."""
    engine = _engine(migration_db)
    try:
        with engine.connect() as conn:
            for name in NEW_TABLES:
                assert _table_exists(conn, name), f"{name} was not created"
            assert _column_exists(conn, "external_user", "active")
    finally:
        engine.dispose()


def _seed_external_user(
    conn: Connection, org_id: uuid.UUID, user_id: uuid.UUID, external_id: str
) -> uuid.UUID:
    row_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO external_user "
            "(id, organization_id, user_id, external_id) "
            "VALUES (:id, :org, :u, :ext)"
        ),
        {"id": row_id, "org": org_id, "u": user_id, "ext": external_id},
    )
    return row_id


def test_external_user_linkage_is_per_organization(migration_db: str) -> None:
    """One user may be linked in two orgs; a subject id is unique within one."""
    engine = _engine(migration_db)
    try:
        with engine.begin() as conn:
            first_org = _seed_org(conn)
            second_org = _seed_org(conn)
            user_id = _seed_user(conn)
            for org_id in (first_org, second_org):
                _seed_external_user(conn, org_id, user_id, "idp-subject-1")

        # One linkage per user per org.
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _seed_external_user(conn, first_org, user_id, "idp-subject-2")

        # Subject ids are unique within a tenant.
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                other = _seed_user(conn)
                _seed_external_user(conn, first_org, other, "idp-subject-1")
    finally:
        engine.dispose()


def test_external_group_is_unique_per_organization(migration_db: str) -> None:
    """Two organizations may sync the same provider group id; one may not twice."""
    engine = _engine(migration_db)
    try:
        with engine.begin() as conn:
            first_org = _seed_org(conn)
            second_org = _seed_org(conn)
            for org_id in (first_org, second_org):
                conn.execute(
                    text(
                        "INSERT INTO external_group "
                        "(id, organization_id, external_id, display_name) "
                        "VALUES (gen_random_uuid(), :org, 'idp-eng', 'Engineering')"
                    ),
                    {"org": org_id},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO external_group "
                        "(id, organization_id, external_id, display_name) "
                        "VALUES (gen_random_uuid(), :org, 'idp-eng', 'Engineering')"
                    ),
                    {"org": first_org},
                )
    finally:
        engine.dispose()


def test_deleting_external_group_cascades_to_members_and_mappings(
    migration_db: str,
) -> None:
    """Shadow state disappears with its parent; the Tracecat group survives."""
    engine = _engine(migration_db)
    try:
        with engine.begin() as conn:
            org_id = _seed_org(conn)
            user_id = _seed_user(conn)
            group_id = _seed_group(conn, org_id)
            external_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO external_group "
                    "(id, organization_id, external_id, display_name) "
                    "VALUES (:id, :org, 'idp-eng', 'Engineering')"
                ),
                {"id": external_id, "org": org_id},
            )
            external_user_id = _seed_external_user(
                conn, org_id, user_id, "idp-subject-1"
            )
            conn.execute(
                text(
                    "INSERT INTO external_group_member "
                    "(external_group_id, external_user_id) VALUES (:eg, :eu)"
                ),
                {"eg": external_id, "eu": external_user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO external_group_mapping "
                    "(id, organization_id, external_group_id, group_id) "
                    "VALUES (gen_random_uuid(), :org, :eg, :g)"
                ),
                {"org": org_id, "eg": external_id, "g": group_id},
            )

        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM external_group WHERE id = :id"), {"id": external_id}
            )
        with engine.connect() as conn:
            for table in ("external_group_member", "external_group_mapping"):
                assert (
                    conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                    == 0
                )
            # The mapping's cascade drops the mapping, never the Tracecat group.
            assert (
                conn.execute(
                    text('SELECT count(*) FROM "group" WHERE id = :g'), {"g": group_id}
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_downgrade_removes_everything_the_upgrade_added(migration_db: str) -> None:
    """The additive migration round-trips: down leaves no trace, up restores it."""
    engine = _engine(migration_db)
    try:
        _run_alembic(migration_db, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            for name in NEW_TABLES:
                assert not _table_exists(conn, name), f"{name} survived downgrade"

        _run_alembic(migration_db, "upgrade", MIGRATION_REVISION)
        with engine.connect() as conn:
            for name in NEW_TABLES:
                assert _table_exists(conn, name), f"{name} was not restored"
    finally:
        engine.dispose()
