"""Tests for SPM finding control identifier repair migration."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

MIGRATION_REVISION = "a46c2f1d9b87"
PREVIOUS_REVISION = "ed7b7d97ede5"
KNOWN_CONTROL_KEY = "claude.mcp_server.approved"
KNOWN_CONTROL_ID = "7dca8397-056a-4cc7-a4a6-3fef782b21a2"
UNKNOWN_CONTROL_KEY = "custom.control.experimental"
UNKNOWN_CONTROL_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "tracecat.spm.unknown-control"
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


def _create_old_spm_finding_schema(db_url: str) -> None:
    engine = create_engine(db_url, poolclass=NullPool)
    organization_id = uuid.uuid4()
    endpoint_id = uuid.uuid4()

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE spm_finding (
                        id UUID NOT NULL,
                        endpoint_id UUID NOT NULL,
                        asset_id UUID NOT NULL,
                        asset_sighting_id UUID,
                        control_id VARCHAR(255) NOT NULL,
                        control_revision VARCHAR(64),
                        harness VARCHAR(32) NOT NULL,
                        asset_class VARCHAR(64) NOT NULL,
                        asset_type VARCHAR(64) NOT NULL,
                        severity VARCHAR(16) NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        summary TEXT NOT NULL,
                        evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
                        enrichment JSONB NOT NULL DEFAULT '{}'::jsonb,
                        recommended_action VARCHAR(64),
                        recommended_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                        opened_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                        closed_at TIMESTAMP WITH TIME ZONE,
                        last_decision_at TIMESTAMP WITH TIME ZONE,
                        organization_id UUID NOT NULL,
                        surrogate_id SERIAL PRIMARY KEY,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                        CONSTRAINT uq_spm_finding_endpoint_asset_control
                            UNIQUE (
                                organization_id,
                                endpoint_id,
                                asset_id,
                                control_id
                            )
                    )
                    """
                )
            )
            rows = [
                {
                    "id": uuid.uuid4(),
                    "organization_id": organization_id,
                    "endpoint_id": endpoint_id,
                    "asset_id": uuid.uuid4(),
                    "control_id": KNOWN_CONTROL_KEY,
                    "summary": "Known SPM control finding",
                },
                {
                    "id": uuid.uuid4(),
                    "organization_id": organization_id,
                    "endpoint_id": endpoint_id,
                    "asset_id": uuid.uuid4(),
                    "control_id": UNKNOWN_CONTROL_KEY,
                    "summary": "Unknown SPM control finding",
                },
            ]
            conn.execute(
                text(
                    """
                    INSERT INTO spm_finding (
                        id,
                        endpoint_id,
                        asset_id,
                        control_id,
                        harness,
                        asset_class,
                        asset_type,
                        severity,
                        status,
                        summary,
                        organization_id
                    )
                    VALUES (
                        :id,
                        :endpoint_id,
                        :asset_id,
                        :control_id,
                        'claude_code',
                        'mcp_server',
                        'mcp_server',
                        'high',
                        'open',
                        :summary,
                        :organization_id
                    )
                    """
                ),
                rows,
            )
    finally:
        engine.dispose()


@pytest.fixture(scope="function")
def migration_db_url() -> Iterator[str]:
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    db_name = f"test_spm_finding_{uuid.uuid4().hex[:8]}"
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
        _create_old_spm_finding_schema(db_url)
        _run_alembic(db_url, "stamp", PREVIOUS_REVISION)
        yield db_url
    finally:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        default_engine.dispose()


def test_spm_finding_control_ids_are_repaired(migration_db_url: str) -> None:
    _run_alembic(migration_db_url, "upgrade", MIGRATION_REVISION)

    engine = create_engine(migration_db_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            columns = {
                row.column_name: row
                for row in conn.execute(
                    text(
                        """
                        SELECT column_name, data_type, is_nullable
                        FROM information_schema.columns
                        WHERE table_name = 'spm_finding'
                          AND column_name IN ('control_id', 'control_key')
                        """
                    )
                )
            }
            assert columns["control_id"].data_type == "uuid"
            assert columns["control_id"].is_nullable == "NO"
            assert columns["control_key"].data_type == "character varying"
            assert columns["control_key"].is_nullable == "NO"

            findings = {
                row.control_key: row.control_id
                for row in conn.execute(
                    text(
                        """
                        SELECT control_key, control_id::text AS control_id
                        FROM spm_finding
                        """
                    )
                )
            }
            assert findings[KNOWN_CONTROL_KEY] == KNOWN_CONTROL_ID
            assert findings[UNKNOWN_CONTROL_KEY] == str(
                uuid.uuid5(UNKNOWN_CONTROL_NAMESPACE, UNKNOWN_CONTROL_KEY)
            )

            constraint_columns = [
                row.column_name
                for row in conn.execute(
                    text(
                        """
                        SELECT kcu.column_name
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON tc.constraint_name = kcu.constraint_name
                         AND tc.table_schema = kcu.table_schema
                        WHERE tc.table_name = 'spm_finding'
                          AND tc.constraint_name = 'uq_spm_finding_endpoint_asset_control'
                          AND tc.constraint_type = 'UNIQUE'
                        ORDER BY kcu.ordinal_position
                        """
                    )
                )
            ]
            assert constraint_columns == [
                "organization_id",
                "endpoint_id",
                "asset_id",
                "control_id",
            ]
    finally:
        engine.dispose()
