"""Tests for tenant RLS on parent-scoped tables (c10b1e9299db).

Invariants:
- At head, every public table has RLS enabled or is in ``GLOBAL_TABLES``.
- Upgrade enables RLS and one policy on each parent-scoped table; downgrade
  removes both again.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.db.tenant_rls import GLOBAL_TABLES, PARENT_SCOPED_TABLES

MIGRATION_REVISION = "c10b1e9299db"
PREVIOUS_REVISION = "11e685c62cfd"


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
    """A fresh database migrated to head."""
    admin = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    name = f"test_parent_scoped_rls_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_DB_CONFIG.test_url_sync.replace(TEST_DB_CONFIG.test_db_name, name)
    try:
        _alembic(url, "upgrade", "head")
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    engine = create_engine(db_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


def _rls_flags(engine: Engine) -> dict[str, bool]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
                """
            )
        ).tuples()
        return dict(rows.all())


def _policy_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename, count(*) FROM pg_policies "
                "WHERE schemaname = 'public' AND tablename = ANY(:tables) "
                "GROUP BY tablename"
            ),
            {"tables": sorted(PARENT_SCOPED_TABLES)},
        ).tuples()
        return dict(rows.all())


def test_every_public_table_has_rls_or_a_global_reason(engine: Engine) -> None:
    flags = _rls_flags(engine)
    without_rls = {table for table, enabled in flags.items() if not enabled}

    assert without_rls == set(GLOBAL_TABLES), (
        "Public tables without RLS must be listed in GLOBAL_TABLES with a reason, "
        "and every GLOBAL_TABLES entry must be a real table without RLS. "
        f"Missing from the allowlist: {sorted(without_rls - set(GLOBAL_TABLES))}; "
        f"stale or RLS-enabled entries: {sorted(set(GLOBAL_TABLES) - without_rls)}"
    )


def test_downgrade_removes_policies_and_upgrade_restores_them(
    db_url: str, engine: Engine
) -> None:
    every_table_once = dict.fromkeys(PARENT_SCOPED_TABLES, 1)
    assert _policy_counts(engine) == every_table_once

    _alembic(db_url, "downgrade", PREVIOUS_REVISION)
    try:
        flags = _rls_flags(engine)
        assert not any(flags[table] for table in PARENT_SCOPED_TABLES)
        assert _policy_counts(engine) == {}
    finally:
        _alembic(db_url, "upgrade", MIGRATION_REVISION)

    flags = _rls_flags(engine)
    assert all(flags[table] for table in PARENT_SCOPED_TABLES)
    assert _policy_counts(engine) == every_table_once
