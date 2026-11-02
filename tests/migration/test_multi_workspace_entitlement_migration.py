"""Tests for the multi-workspace entitlement backfill migration."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "31ee4b7f175a"
PREVIOUS_REVISION = "526f867f6a75"


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
    db_name = f"test_multi_ws_ent_{uuid.uuid4().hex[:8]}"
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


def test_multi_workspace_backfill_only_fills_missing_key(
    migration_db_url: str,
) -> None:
    """Tiers without the key get `true`; any stored value, including JSON null, is kept.

    The migration exists so hosted tiers created before the entitlement keep
    their workspace-creation behavior, while never overriding a choice an
    operator already stored. Sibling keys must survive the write.
    """
    absent_id = uuid.uuid4()
    explicit_false_id = uuid.uuid4()
    explicit_true_id = uuid.uuid4()
    json_null_id = uuid.uuid4()

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO tier (id, display_name, entitlements)
                    VALUES
                        (:absent_id, 'absent', '{"git_sync": true}'::jsonb),
                        (
                            :explicit_false_id,
                            'explicit false',
                            '{"git_sync": true, "multi_workspace": false}'::jsonb
                        ),
                        (
                            :explicit_true_id,
                            'explicit true',
                            '{"multi_workspace": true}'::jsonb
                        ),
                        (
                            :json_null_id,
                            'json null',
                            '{"multi_workspace": null}'::jsonb
                        )
                    """
                ),
                {
                    "absent_id": absent_id,
                    "explicit_false_id": explicit_false_id,
                    "explicit_true_id": explicit_true_id,
                    "json_null_id": json_null_id,
                },
            )

        _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, entitlements FROM tier WHERE id = ANY(:ids)"),
                {
                    "ids": [
                        absent_id,
                        explicit_false_id,
                        explicit_true_id,
                        json_null_id,
                    ]
                },
            ).all()
    finally:
        engine.dispose()

    entitlements = {row.id: row.entitlements for row in rows}
    assert entitlements == {
        absent_id: {"git_sync": True, "multi_workspace": True},
        explicit_false_id: {"git_sync": True, "multi_workspace": False},
        explicit_true_id: {"multi_workspace": True},
        json_null_id: {"multi_workspace": None},
    }
