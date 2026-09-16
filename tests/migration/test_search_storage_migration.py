"""Live migration prerequisite, source compatibility and downgrade checks."""

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG


def run_alembic(url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        env={**os.environ, "TRACECAT__DB_URI": url},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("preinstalled", [False, True])
def test_search_migration_enables_vector_and_preserves_source_data(
    preinstalled: bool,
) -> None:
    name = f"test_search_migration_{uuid.uuid4().hex}"
    admin = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    url = TEST_DB_CONFIG.test_url_sync.replace(TEST_DB_CONFIG.test_db_name, name)
    engine = create_engine(url, poolclass=NullPool)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        previous = run_alembic(url, "upgrade", "31ee4b7f175a")
        assert previous.returncode == 0, previous.stderr
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE synthetic_source (id integer PRIMARY KEY, body text NOT NULL)"
                )
            )
            conn.execute(
                text("INSERT INTO synthetic_source VALUES (1, 'preserved source')")
            )
        with engine.begin() as conn:
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
                )
                == 0
            )
            if preinstalled:
                conn.execute(text("CREATE EXTENSION vector WITH SCHEMA public"))
        upgraded = run_alembic(url, "upgrade", "9680c861644a")
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.begin() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_extension e "
                        "JOIN pg_namespace n ON n.oid = e.extnamespace "
                        "WHERE e.extname = 'vector' AND n.nspname = 'public'"
                    )
                )
                == 1
            )
            assert (
                conn.scalar(text("SELECT body FROM synthetic_source WHERE id = 1"))
                == "preserved source"
            )
            conn.execute(
                text(
                    "INSERT INTO synthetic_source VALUES (2, 'old writer still works')"
                )
            )
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_policies WHERE tablename LIKE 'search_%'"
                    )
                )
                == 5
            )
            assert conn.scalar(text("SELECT count(*) FROM search_workspace_state")) == 0
        downgraded = run_alembic(url, "downgrade", "31ee4b7f175a")
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT count(*) FROM synthetic_source")) == 2
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
                )
                == 1
            )
            assert conn.scalar(text("SELECT to_regclass('search_chunk')")) is None
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
