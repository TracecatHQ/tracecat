"""Tests for the ``agent_session (id, workspace_id)`` unique key migration."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.db.models import Base

MIGRATION_REVISION = "ec392d96f2b0"
PREVIOUS_REVISION = "4a17af960e05"
CONSTRAINT = "uq_agent_session_id_workspace_id"


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
    name = f"test_session_ws_key_{uuid.uuid4().hex[:8]}"
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


def _constraint(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = :name AND conrelid = 'agent_session'::regclass"
            ),
            {"name": CONSTRAINT},
        ).scalar_one_or_none()


def _only_agent_session(
    _object: object,
    name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: object,
) -> bool:
    return type_ != "table" or name == "agent_session"


def _model_drift(engine: Engine) -> list[object]:
    """Autogenerate diffs naming this constraint (``agent_session`` has older drift)."""
    with engine.connect() as conn:
        context = MigrationContext.configure(
            conn, opts={"include_object": _only_agent_session}
        )
        diffs = compare_metadata(context, Base.metadata)
    return [d for d in diffs if getattr(d[1], "name", None) == CONSTRAINT]


def test_upgrade_matches_the_model_retries_and_downgrades(db_url: str) -> None:
    engine = create_engine(db_url, poolclass=NullPool)
    try:
        assert _model_drift(engine)
        with engine.begin() as conn:
            # A failed concurrent build leaves an index the retry must replace.
            conn.execute(text(f'CREATE INDEX "{CONSTRAINT}" ON agent_session (id)'))
        _alembic(db_url, "upgrade", MIGRATION_REVISION)
        assert _constraint(engine) == "UNIQUE (id, workspace_id)"
        assert _model_drift(engine) == []

        _alembic(db_url, "downgrade", PREVIOUS_REVISION)
        assert _constraint(engine) is None
        _alembic(db_url, "upgrade", MIGRATION_REVISION)
        assert _constraint(engine) == "UNIQUE (id, workspace_id)"
    finally:
        engine.dispose()
