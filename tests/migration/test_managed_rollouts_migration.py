"""Tests for the managed rollout, preset skill binding and detached copy migration."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.db.models import Base

MIGRATION_REVISION = "4a17af960e05"
PREVIOUS_REVISION = "8c04c3aa6e35"
NEW_TABLES = (
    "managed_rollout",
    "managed_preset_skill_binding",
    "managed_detached_copy",
)
# Written only by the rollout engine; tenants may read them.
READ_ONLY_TABLES = ("managed_rollout", "managed_preset_skill_binding")
# managed_resource gains rollout_id, so the model comparison includes it too.
COMPARED_TABLES = (*NEW_TABLES, "managed_resource")
# Operation names alembic_postgresql_enum puts first in its diff tuples.
_POSTGRESQL_ENUM_DIFFS = frozenset({"create_enum", "drop_enum", "change_enum_variants"})


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for migration tests."""
    yield


def _alembic(db_url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "TRACECAT__DB_URI": db_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    )


@pytest.fixture(scope="module")
def db_url() -> Iterator[str]:
    """A fresh database migrated to the revision before this one."""
    admin = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    name = f"test_managed_rollouts_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        url = TEST_DB_CONFIG.test_url_sync.replace(TEST_DB_CONFIG.test_db_name, name)
        _alembic(url, "upgrade", PREVIOUS_REVISION)
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _existing_tables(conn: Connection) -> set[str]:
    inspector = inspect(conn)
    return {table for table in NEW_TABLES if inspector.has_table(table)}


def _include_compared_tables(
    _object: object,
    name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: object,
) -> bool:
    return type_ != "table" or name in COMPARED_TABLES


def _rls_enabled(conn: Connection, table: str) -> bool:
    return conn.execute(
        text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
    ).scalar_one()


def _has_rollout_id(conn: Connection) -> bool:
    return "rollout_id" in {
        column["name"] for column in inspect(conn).get_columns("managed_resource")
    }


def test_upgrade_matches_the_model_and_downgrade_removes_the_tables(
    db_url: str,
) -> None:
    engine = create_engine(db_url, poolclass=NullPool)
    try:
        _alembic(db_url, "upgrade", MIGRATION_REVISION)
        with engine.connect() as conn:
            assert _existing_tables(conn) == set(NEW_TABLES)
            assert _has_rollout_id(conn)
            context = MigrationContext.configure(
                conn,
                opts={
                    "include_object": _include_compared_tables,
                    "compare_type": True,
                    "compare_server_default": True,
                },
            )
            diffs = [
                diff
                for diff in compare_metadata(context, Base.metadata)
                if not (isinstance(diff, tuple) and diff[0] in _POSTGRESQL_ENUM_DIFFS)
            ]
            assert diffs == []
            for table in NEW_TABLES:
                assert _rls_enabled(conn, table)
            # Tenants read their workspace's rows but only the RLS-bypass
            # session writes: a FOR ALL policy gated on the bypass setting,
            # plus a FOR SELECT policy scoped to the workspace.
            for table in READ_ONLY_TABLES:
                policies = dict(
                    conn.execute(
                        text("SELECT cmd, qual FROM pg_policies WHERE tablename = :t"),
                        {"t": table},
                    )
                    .tuples()
                    .all()
                )
                assert set(policies) == {"ALL", "SELECT"}
                assert "app.rls_bypass" in policies["ALL"]
                assert "app.current_workspace_id" in policies["SELECT"]

        _alembic(db_url, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            assert _existing_tables(conn) == set()
            assert not _has_rollout_id(conn)
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_policies"
                        " WHERE tablename = ANY(:tables)"
                    ),
                    {"tables": list(NEW_TABLES)},
                ).scalar_one()
                == 0
            )

        # The downgrade left nothing behind that blocks a second upgrade.
        _alembic(db_url, "upgrade", MIGRATION_REVISION)
        with engine.connect() as conn:
            assert _existing_tables(conn) == set(NEW_TABLES)
    finally:
        engine.dispose()
