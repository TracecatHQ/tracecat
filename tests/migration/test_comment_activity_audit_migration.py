"""Tests for comment activity event migration."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "3b58a1430e95"
PREVIOUS_REVISION = "b42892363e72"


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
    db_name = f"test_comment_activity_{uuid.uuid4().hex[:8]}"
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


def test_comment_activity_migration_adds_comment_event_types_without_audit_table(
    migration_db_url: str,
) -> None:
    _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

    engine = create_engine(migration_db_url, poolclass=NullPool)
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()
    comment_id = uuid.uuid4()

    try:
        with engine.begin() as conn:
            audit_table_count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_name = 'audit_event'
                    """
                )
            ).scalar_one()
            assert audit_table_count == 0

            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, 'Test org', :slug, true)
                    """
                ),
                {"id": organization_id, "slug": f"test-org-{organization_id.hex[:8]}"},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES (:id, :organization_id, 'Test workspace')
                    """
                ),
                {"id": workspace_id, "organization_id": organization_id},
            )
            conn.execute(
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
                        'Comment activity case',
                        'Comment activity case',
                        'MEDIUM',
                        'LOW',
                        'NEW'
                    )
                    """
                ),
                {"id": case_id, "workspace_id": workspace_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO case_event (
                        id,
                        workspace_id,
                        case_id,
                        type,
                        data,
                        user_id
                    )
                    VALUES (
                        :id,
                        :workspace_id,
                        :case_id,
                        'COMMENT_CREATED',
                        CAST(:data AS jsonb),
                        NULL
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "workspace_id": workspace_id,
                    "case_id": case_id,
                    "data": (
                        f'{{"comment_id":"{comment_id}","parent_id":null,'
                        f'"thread_root_id":"{comment_id}"}}'
                    ),
                },
            )

            event_types = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT enumlabel
                        FROM pg_enum
                        JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                        WHERE pg_type.typname = 'caseeventtype'
                        """
                    )
                )
            }
            assert {
                "COMMENT_CREATED",
                "COMMENT_UPDATED",
                "COMMENT_DELETED",
                "COMMENT_REPLY_CREATED",
                "COMMENT_REPLY_UPDATED",
                "COMMENT_REPLY_DELETED",
            } <= event_types
    finally:
        engine.dispose()


def test_comment_activity_migration_downgrade_removes_comment_event_types(
    migration_db_url: str,
) -> None:
    _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

    engine = create_engine(migration_db_url, poolclass=NullPool)
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()
    comment_event_id = uuid.uuid4()
    comment_id = uuid.uuid4()

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active)
                    VALUES (:id, 'Test org', :slug, true)
                    """
                ),
                {"id": organization_id, "slug": f"test-org-{organization_id.hex[:8]}"},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO workspace (id, organization_id, name)
                    VALUES (:id, :organization_id, 'Test workspace')
                    """
                ),
                {"id": workspace_id, "organization_id": organization_id},
            )
            conn.execute(
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
                        'Comment activity case',
                        'Comment activity case',
                        'MEDIUM',
                        'LOW',
                        'NEW'
                    )
                    """
                ),
                {"id": case_id, "workspace_id": workspace_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO case_event (
                        id,
                        workspace_id,
                        case_id,
                        type,
                        data,
                        user_id
                    )
                    VALUES (
                        :id,
                        :workspace_id,
                        :case_id,
                        'COMMENT_REPLY_DELETED',
                        CAST(:data AS jsonb),
                        NULL
                    )
                    """
                ),
                {
                    "id": comment_event_id,
                    "workspace_id": workspace_id,
                    "case_id": case_id,
                    "data": (
                        f'{{"comment_id":"{comment_id}","parent_id":"{comment_id}",'
                        f'"thread_root_id":"{comment_id}","delete_mode":"hard"}}'
                    ),
                },
            )

        _run_alembic(migration_db_url, "downgrade", PREVIOUS_REVISION)

        with engine.begin() as conn:
            event_count = conn.execute(
                text("SELECT COUNT(*) FROM case_event WHERE id = :id"),
                {"id": comment_event_id},
            ).scalar_one()
            assert event_count == 0

            event_types = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT enumlabel
                        FROM pg_enum
                        JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                        WHERE pg_type.typname = 'caseeventtype'
                        """
                    )
                )
            }
            assert "COMMENT_CREATED" not in event_types
            assert "COMMENT_REPLY_DELETED" not in event_types

            audit_table_count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_name = 'audit_event'
                    """
                )
            ).scalar_one()
            assert audit_table_count == 0
    finally:
        engine.dispose()


def test_comment_activity_migration_downgrade_preserves_unowned_audit_event_table(
    migration_db_url: str,
) -> None:
    _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

    engine = create_engine(migration_db_url, poolclass=NullPool)
    audit_event_id = uuid.uuid4()

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE audit_event (
                        id UUID NOT NULL PRIMARY KEY,
                        organization_id UUID,
                        workspace_id UUID,
                        actor_type VARCHAR(32) NOT NULL,
                        actor_id UUID NOT NULL,
                        actor_label VARCHAR(255),
                        ip_address VARCHAR(64),
                        resource_type VARCHAR(64) NOT NULL,
                        resource_id UUID,
                        action VARCHAR(32) NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        data JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO audit_event (
                        id,
                        actor_type,
                        actor_id,
                        resource_type,
                        action,
                        status,
                        data
                    )
                    VALUES (
                        :id,
                        'USER',
                        :actor_id,
                        'case_comment',
                        'create',
                        'SUCCESS',
                        '{}'::jsonb
                    )
                    """
                ),
                {"id": audit_event_id, "actor_id": uuid.uuid4()},
            )

        _run_alembic(migration_db_url, "downgrade", PREVIOUS_REVISION)

        with engine.begin() as conn:
            audit_table_count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_name = 'audit_event'
                    """
                )
            ).scalar_one()
            assert audit_table_count == 1
            persisted_id = conn.execute(
                text("SELECT id FROM audit_event WHERE id = :id"),
                {"id": audit_event_id},
            ).scalar_one()
            assert persisted_id == audit_event_id
    finally:
        engine.dispose()
