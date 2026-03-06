"""Tests for workspace-scoped case number migration."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "13cfd6e83e36"
PREVIOUS_REVISION = "8e2a638ae873"


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
    db_name = f"test_case_numbers_{uuid.uuid4().hex[:8]}"
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


def _seed_pre_migration_cases(db_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    engine = create_engine(db_url, poolclass=NullPool)
    organization_id = uuid.uuid4()
    workspace_a_id = uuid.uuid4()
    workspace_b_id = uuid.uuid4()

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, :name, :slug, true)
                    """
                ),
                {
                    "id": organization_id,
                    "name": "Test organization",
                    "slug": f"test-org-{organization_id.hex[:8]}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES
                        (:workspace_a_id, :organization_id, 'Workspace A'),
                        (:workspace_b_id, :organization_id, 'Workspace B')
                    """
                ),
                {
                    "workspace_a_id": workspace_a_id,
                    "workspace_b_id": workspace_b_id,
                    "organization_id": organization_id,
                },
            )
            conn.execute(
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
                    VALUES
                        (:case_a1_id, :workspace_a_id, 1, 'A-1', 'A-1', 'MEDIUM', 'LOW', 'NEW'),
                        (:case_b1_id, :workspace_b_id, 2, 'B-2', 'B-2', 'MEDIUM', 'LOW', 'NEW'),
                        (:case_a2_id, :workspace_a_id, 3, 'A-3', 'A-3', 'MEDIUM', 'LOW', 'NEW'),
                        (:case_b2_id, :workspace_b_id, 4, 'B-4', 'B-4', 'MEDIUM', 'LOW', 'NEW')
                    """
                ),
                {
                    "case_a1_id": uuid.uuid4(),
                    "case_b1_id": uuid.uuid4(),
                    "case_a2_id": uuid.uuid4(),
                    "case_b2_id": uuid.uuid4(),
                    "workspace_a_id": workspace_a_id,
                    "workspace_b_id": workspace_b_id,
                },
            )
    finally:
        engine.dispose()

    return workspace_a_id, workspace_b_id


def test_case_numbers_are_scoped_to_workspace(migration_db_url: str) -> None:
    workspace_a_id, workspace_b_id = _seed_pre_migration_cases(migration_db_url)
    _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            workspace_a_cases = conn.execute(
                text(
                    """
                    SELECT summary, case_number
                    FROM "case"
                    WHERE workspace_id = :workspace_id
                    ORDER BY case_number
                    """
                ),
                {"workspace_id": workspace_a_id},
            ).all()
            workspace_b_cases = conn.execute(
                text(
                    """
                    SELECT summary, case_number
                    FROM "case"
                    WHERE workspace_id = :workspace_id
                    ORDER BY case_number
                    """
                ),
                {"workspace_id": workspace_b_id},
            ).all()

            assert workspace_a_cases == [("A-1", 1), ("A-3", 2)]
            assert workspace_b_cases == [("B-2", 1), ("B-4", 2)]

            workspace_counters = (
                conn.execute(
                    text(
                        """
                    SELECT id, last_case_number
                    FROM workspace
                    WHERE id = :workspace_a_id
                       OR id = :workspace_b_id
                    """
                    ),
                    {
                        "workspace_a_id": workspace_a_id,
                        "workspace_b_id": workspace_b_id,
                    },
                )
                .tuples()
                .all()
            )
            assert dict(workspace_counters) == {
                workspace_a_id: 2,
                workspace_b_id: 2,
            }

            duplicate_case_numbers = conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM "case"
                    WHERE case_number = 1
                    """
                )
            ).scalar_one()
            assert duplicate_case_numbers == 2

            identity_generation = conn.execute(
                text(
                    """
                    SELECT identity_generation
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'case'
                      AND column_name = 'case_number'
                    """
                )
            ).scalar_one()
            assert identity_generation is None

            unique_constraint_count = conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.table_constraints
                    WHERE table_schema = 'public'
                      AND table_name = 'case'
                      AND constraint_name = 'uq_case_workspace_case_number'
                      AND constraint_type = 'UNIQUE'
                    """
                )
            ).scalar_one()
            assert unique_constraint_count == 1

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
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
                            1,
                            'duplicate',
                            'duplicate',
                            'MEDIUM',
                            'LOW',
                            'NEW'
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "workspace_id": workspace_a_id,
                    },
                )
    finally:
        engine.dispose()
