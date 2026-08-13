"""Tests for deferred workspace case-number allocation."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "2792569cf359"
PREVIOUS_REVISION = "864d277bedfa"
MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "2792569cf359_defer_case_number_allocation.py"
)


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


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "deferred_case_number_allocation_migration",
        MIGRATION_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load migration at {MIGRATION_PATH}")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


@pytest.fixture(scope="function")
def migration_db_url() -> Iterator[str]:
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    db_name = f"test_deferred_case_number_{uuid.uuid4().hex[:8]}"
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


def _insert_case(
    conn: Connection,
    *,
    workspace_id: uuid.UUID,
    case_number: int | None,
) -> tuple[uuid.UUID, int]:
    case_id = uuid.uuid4()
    if case_number is None:
        assigned_number = conn.execute(
            text(
                """
                INSERT INTO "case" (
                    id,
                    workspace_id,
                    summary,
                    description,
                    priority,
                    severity,
                    status
                )
                VALUES (
                    :id,
                    :workspace_id,
                    'Migration case',
                    'Migration case',
                    'MEDIUM',
                    'LOW',
                    'NEW'
                )
                RETURNING case_number
                """
            ),
            {"id": case_id, "workspace_id": workspace_id},
        ).scalar_one()
    else:
        assigned_number = conn.execute(
            text(
                """
                INSERT INTO "case" (
                    id,
                    workspace_id,
                    case_number,
                    summary,
                    description,
                    priority,
                    severity,
                    status
                )
                VALUES (
                    :id,
                    :workspace_id,
                    :case_number,
                    'Migration case',
                    'Migration case',
                    'MEDIUM',
                    'LOW',
                    'NEW'
                )
                RETURNING case_number
                """
            ),
            {
                "id": case_id,
                "workspace_id": workspace_id,
                "case_number": case_number,
            },
        ).scalar_one()
    return case_id, assigned_number


def _constraint_state(conn: Connection) -> tuple[bool, bool]:
    row = conn.execute(
        text(
            """
            SELECT condeferrable, condeferred
            FROM pg_constraint
            WHERE conrelid = '"case"'::regclass
              AND conname = 'uq_case_workspace_case_number'
            """
        )
    ).one()
    return bool(row[0]), bool(row[1])


def _guard_trigger_count(conn: Connection) -> int:
    return conn.execute(
        text(
            """
            SELECT count(*)
            FROM pg_trigger
            WHERE tgrelid = '"case"'::regclass
              AND tgname = 'trg_case_require_assigned_number'
              AND NOT tgisinternal
            """
        )
    ).scalar_one()


def _seed_workspace(db_url: str) -> uuid.UUID:
    engine = create_engine(db_url, poolclass=NullPool)
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, 'Migration test org', :slug, true)
                    """
                ),
                {
                    "id": organization_id,
                    "slug": f"migration-test-{organization_id.hex[:8]}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES (:id, :organization_id, 'Migration test workspace')
                    """
                ),
                {"id": workspace_id, "organization_id": organization_id},
            )
            _, assigned_number = _insert_case(
                conn,
                workspace_id=workspace_id,
                case_number=1,
            )
            assert assigned_number == 1
    finally:
        engine.dispose()
    return workspace_id


def test_deferred_allocator_upgrade_downgrade_and_reupgrade(
    migration_db_url: str,
) -> None:
    workspace_id = _seed_workspace(migration_db_url)
    _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)
    engine = create_engine(migration_db_url, poolclass=NullPool)

    try:
        with engine.connect() as conn:
            assert _constraint_state(conn) == (True, True)
            assert _guard_trigger_count(conn) == 1

        with engine.begin() as conn:
            _, legacy_number = _insert_case(
                conn,
                workspace_id=workspace_id,
                case_number=None,
            )
            assert legacy_number == 2

        first = engine.connect()
        second = engine.connect()
        first_tx = first.begin()
        second_tx = second.begin()
        try:
            first.execute(text("SET LOCAL lock_timeout = '1s'"))
            second.execute(text("SET LOCAL lock_timeout = '1s'"))
            first_case_id, first_pending_number = _insert_case(
                first,
                workspace_id=workspace_id,
                case_number=0,
            )
            second_case_id, second_pending_number = _insert_case(
                second,
                workspace_id=workspace_id,
                case_number=0,
            )
            assert first_pending_number == second_pending_number == 0

            first_number = first.execute(
                text(
                    """
                    UPDATE workspace
                    SET last_case_number = last_case_number + 1
                    WHERE id = :workspace_id
                    RETURNING last_case_number
                    """
                ),
                {"workspace_id": workspace_id},
            ).scalar_one()
            first.execute(
                text('UPDATE "case" SET case_number = :number WHERE id = :id'),
                {"number": first_number, "id": first_case_id},
            )
            first_tx.commit()

            second_number = second.execute(
                text(
                    """
                    UPDATE workspace
                    SET last_case_number = last_case_number + 1
                    WHERE id = :workspace_id
                    RETURNING last_case_number
                    """
                ),
                {"workspace_id": workspace_id},
            ).scalar_one()
            second.execute(
                text('UPDATE "case" SET case_number = :number WHERE id = :id'),
                {"number": second_number, "id": second_case_id},
            )
            second_tx.commit()
        finally:
            if first_tx.is_active:
                first_tx.rollback()
            if second_tx.is_active:
                second_tx.rollback()
            first.close()
            second.close()

        assert (first_number, second_number) == (3, 4)
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _insert_case(conn, workspace_id=workspace_id, case_number=0)

        _run_alembic(migration_db_url, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            assert _constraint_state(conn) == (False, False)
            assert _guard_trigger_count(conn) == 0

        with engine.begin() as conn:
            _, downgraded_number = _insert_case(
                conn,
                workspace_id=workspace_id,
                case_number=None,
            )
            assert downgraded_number == 5

        _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)
        with engine.connect() as conn:
            assert _constraint_state(conn) == (True, True)
            assert _guard_trigger_count(conn) == 1

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _insert_case(conn, workspace_id=workspace_id, case_number=0)
    finally:
        engine.dispose()


def test_downgrade_replaces_constraint_before_disabling_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    allocator_installed = False

    def fail_constraint_replacement(*, deferred: bool) -> None:
        assert deferred is False
        raise RuntimeError("Synthetic concurrent-index failure")

    def install_immediate_allocator() -> None:
        nonlocal allocator_installed
        allocator_installed = True

    monkeypatch.setattr(
        migration,
        "_replace_case_number_constraint",
        fail_constraint_replacement,
    )
    monkeypatch.setattr(
        migration,
        "_install_immediate_allocator",
        install_immediate_allocator,
    )

    with pytest.raises(RuntimeError, match="Synthetic concurrent-index failure"):
        migration.downgrade()

    assert allocator_installed is False
