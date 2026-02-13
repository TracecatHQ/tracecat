"""Live tests for PostgreSQL advisory lock behavior used by Alembic migrations."""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.db.migration_lock import (
    MIGRATION_ADVISORY_LOCK_ID,
    MIGRATION_ADVISORY_LOCK_NAMESPACE,
    acquire_migration_advisory_lock,
    migration_advisory_lock_id,
    release_migration_advisory_lock,
)

EXPECTED_MIGRATION_ADVISORY_LOCK_ID = 5978149383827159724


@pytest.fixture
def pg_engine(db: None) -> Iterator[Engine]:
    """Create a direct PostgreSQL engine for advisory lock integration tests."""
    engine = create_engine(TEST_DB_CONFIG.test_url_sync, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def isolated_migration_db_url() -> Iterator[str]:
    """Create a disposable database for subprocess Alembic integration tests."""
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    test_db_name = f"test_migration_lock_{uuid.uuid4().hex[:8]}"

    termination_query = text(
        f"""
        SELECT pg_terminate_backend(pg_stat_activity.pid)
        FROM pg_stat_activity
        WHERE pg_stat_activity.datname = '{test_db_name}'
        AND pid <> pg_backend_pid();
        """
    )

    try:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'CREATE DATABASE "{test_db_name}"'))

        test_db_url = TEST_DB_CONFIG.test_url_sync.replace(
            TEST_DB_CONFIG.test_db_name, test_db_name
        )
        yield test_db_url
    finally:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}"'))
        default_engine.dispose()


def _run_alembic_command(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = db_url
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_migration_advisory_lock_id_is_stable() -> None:
    """Lock id generation should be deterministic and in bigint range."""
    lock_id = migration_advisory_lock_id(MIGRATION_ADVISORY_LOCK_NAMESPACE)
    assert lock_id == EXPECTED_MIGRATION_ADVISORY_LOCK_ID
    assert lock_id == MIGRATION_ADVISORY_LOCK_ID
    assert 1 <= lock_id <= (2**63 - 1)


@pytest.mark.integration
def test_migration_lock_serializes_concurrent_sessions(pg_engine: Engine) -> None:
    """Second session cannot acquire migration lock while first session holds it."""
    with pg_engine.connect() as holder, pg_engine.connect() as contender:
        acquired_by_holder = acquire_migration_advisory_lock(holder)
        assert acquired_by_holder is True

        contender_acquired = contender.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
        ).scalar_one()
        assert contender_acquired is False

        release_migration_advisory_lock(holder)

        contender_acquired_after_release = contender.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
        ).scalar_one()
        assert contender_acquired_after_release is True

        contender.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
        )


@pytest.mark.integration
def test_migration_lock_blocks_until_released(pg_engine: Engine) -> None:
    """Blocking lock acquisition should wait until current holder releases."""
    with pg_engine.connect() as holder:
        acquired_by_holder = acquire_migration_advisory_lock(holder)
        assert acquired_by_holder is True

        def acquire_in_new_session() -> bool:
            with pg_engine.connect() as contender:
                acquired = acquire_migration_advisory_lock(contender)
                release_migration_advisory_lock(contender)
                return acquired

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(acquire_in_new_session)
            time.sleep(0.5)
            assert future.done() is False

            release_migration_advisory_lock(holder)
            assert future.result(timeout=5) is True


@pytest.mark.integration
def test_migration_lock_is_released_when_session_closes(pg_engine: Engine) -> None:
    """Connection close releases session-level advisory locks."""
    holder = pg_engine.connect()
    try:
        acquired_by_holder = acquire_migration_advisory_lock(holder)
        assert acquired_by_holder is True
    finally:
        holder.close()

    with pg_engine.connect() as contender:
        contender_acquired = contender.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
        ).scalar_one()
        assert contender_acquired is True
        contender.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
        )


@pytest.mark.integration
def test_alembic_upgrade_waits_for_lock_release(
    isolated_migration_db_url: str,
) -> None:
    """A real Alembic upgrade process should block while migration lock is held."""
    stamp_result = _run_alembic_command(isolated_migration_db_url, "stamp", "head")
    assert stamp_result.returncode == 0, (
        "Alembic stamp failed.\n"
        f"stdout:\n{stamp_result.stdout}\n"
        f"stderr:\n{stamp_result.stderr}"
    )

    engine = create_engine(isolated_migration_db_url, poolclass=NullPool)
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = isolated_migration_db_url

    with engine.connect() as holder:
        acquired_by_holder = acquire_migration_advisory_lock(holder)
        assert acquired_by_holder is True

        proc = subprocess.Popen(
            ["uv", "run", "alembic", "upgrade", "head"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(1.0)
            assert proc.poll() is None
        finally:
            release_migration_advisory_lock(holder)

        stdout, stderr = proc.communicate(timeout=30)
        assert proc.returncode == 0, (
            "Alembic upgrade failed.\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )
    engine.dispose()
