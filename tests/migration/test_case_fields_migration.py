"""Tests for case_fields migration to workspace-specific schemas.

This module tests the migration (b4d8b2f2c9dd) by running the actual
alembic migration commands against a test database.
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.database import TEST_DB_CONFIG
from tracecat.identifiers.workflow import WorkspaceUUID

# Test-specific organization ID
TEST_ORG_ID = uuid.uuid4()

# Constants
PUBLIC_SCHEMA = "public"
TABLE_NAME = "case_fields"
BASE_COLUMNS = {"id", "case_id", "created_at", "updated_at", "workspace_id"}
MIGRATION_REVISION = "b4d8b2f2c9dd"
PREVIOUS_REVISION = "a6c2d9e7f5b1"
SCHEMA_PREFIX = "custom_fields_"


def _workspace_schema(workspace_id: uuid.UUID) -> str:
    """Generate workspace-specific schema name."""
    ws_short = WorkspaceUUID.new(workspace_id).short()
    return f"{SCHEMA_PREFIX}{ws_short}"


def _has_fk_to_cases(conn, schema: str) -> bool:
    """Check whether the given table has a FK from case_id to public.cases."""
    result = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = :schema
              AND tc.table_name = :table
              AND tc.constraint_type = 'FOREIGN KEY'
              AND kcu.column_name = 'case_id'
            """
        ),
        {"schema": schema, "table": TABLE_NAME},
    )
    return result.scalar_one() > 0


def _has_unique_case_id(conn, schema: str) -> bool:
    """Check whether the given table has a UNIQUE constraint on case_id."""
    result = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = :schema
              AND tc.table_name = :table
              AND tc.constraint_type = 'UNIQUE'
              AND kcu.column_name = 'case_id'
            """
        ),
        {"schema": schema, "table": TABLE_NAME},
    )
    return result.scalar_one() > 0


def _run_alembic_upgrade(db_url: str, revision: str = MIGRATION_REVISION) -> None:
    """Run alembic upgrade to a specific revision."""
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = db_url
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", revision],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Alembic upgrade failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


def _run_alembic_downgrade(db_url: str, revision: str = PREVIOUS_REVISION) -> None:
    """Run alembic downgrade to a specific revision."""
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = db_url
    result = subprocess.run(
        ["uv", "run", "alembic", "downgrade", revision],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Alembic downgrade failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


def _run_alembic_stamp(db_url: str, revision: str) -> None:
    """Stamp the database with a specific revision without running migrations."""
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = db_url
    result = subprocess.run(
        ["uv", "run", "alembic", "stamp", revision],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Alembic stamp failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


@pytest.fixture(scope="function")
def test_db():
    """Create a test database with pre-migration state."""
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT"
    )

    test_db_name = f"test_migration_{uuid.uuid4().hex[:8]}"

    termination_query = text(
        f"""
        SELECT pg_terminate_backend(pg_stat_activity.pid)
        FROM pg_stat_activity
        WHERE pg_stat_activity.datname = '{test_db_name}'
        AND pid <> pg_backend_pid();
        """
    )

    try:
        # Create database
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'CREATE DATABASE "{test_db_name}"'))

        test_url_sync = TEST_DB_CONFIG.test_url_sync.replace(
            TEST_DB_CONFIG.test_db_name, test_db_name
        )

        # Create tables in pre-migration state
        test_engine = create_engine(test_url_sync)
        with test_engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            # Create workspace table
            conn.execute(
                text(
                    """
                    CREATE TABLE workspace (
                        id UUID PRIMARY KEY,
                        name VARCHAR NOT NULL,
                        owner_id UUID NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        settings JSONB DEFAULT '{}'::jsonb
                    )
                    """
                )
            )

            # Create cases table
            conn.execute(
                text(
                    """
                    CREATE TABLE cases (
                        id UUID PRIMARY KEY,
                        workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
                        summary VARCHAR NOT NULL,
                        description TEXT NOT NULL,
                        status VARCHAR NOT NULL,
                        priority VARCHAR NOT NULL,
                        severity VARCHAR NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                    """
                )
            )

            # Create case_fields table in pre-migration state (with workspace_id)
            conn.execute(
                text(
                    """
                    CREATE TABLE case_fields (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                        workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                    """
                )
            )

        test_engine.dispose()

        # Stamp the database with the previous revision so alembic knows where we are
        _run_alembic_stamp(test_url_sync, PREVIOUS_REVISION)

        yield test_url_sync

    finally:
        # Cleanup
        with default_engine.begin() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}"'))
        default_engine.dispose()


@pytest.fixture
def sample_data(test_db: str) -> dict[str, list[uuid.UUID]]:
    """Insert sample data into the pre-migration database."""
    engine = create_engine(test_db)

    workspace1_id = uuid.uuid4()
    workspace2_id = uuid.uuid4()
    workspace3_id = uuid.uuid4()

    case1_ws1_id = uuid.uuid4()
    case2_ws1_id = uuid.uuid4()
    case1_ws2_id = uuid.uuid4()

    with engine.begin() as conn:
        # Insert workspaces
        conn.execute(
            text(
                """
                INSERT INTO workspace (id, name, owner_id, settings)
                VALUES
                    (:id1, :name1, :owner_id, :settings),
                    (:id2, :name2, :owner_id, :settings),
                    (:id3, :name3, :owner_id, :settings)
                """
            ),
            {
                "id1": workspace1_id,
                "name1": "test-workspace-1",
                "id2": workspace2_id,
                "name2": "test-workspace-2",
                "id3": workspace3_id,
                "name3": "test-workspace-3-empty",
                "owner_id": TEST_ORG_ID,
                "settings": "{}",
            },
        )

        # Insert cases
        conn.execute(
            text(
                """
                INSERT INTO cases (id, workspace_id, summary, description, status, priority, severity)
                VALUES
                    (:id1, :ws1_id, 'Case 1', 'Desc 1', 'NEW', 'MEDIUM', 'LOW'),
                    (:id2, :ws1_id, 'Case 2', 'Desc 2', 'IN_PROGRESS', 'HIGH', 'MEDIUM'),
                    (:id3, :ws2_id, 'Case 3', 'Desc 3', 'NEW', 'LOW', 'LOW')
                """
            ),
            {
                "id1": case1_ws1_id,
                "ws1_id": workspace1_id,
                "id2": case2_ws1_id,
                "id3": case1_ws2_id,
                "ws2_id": workspace2_id,
            },
        )

        # Insert case_fields
        conn.execute(
            text(
                """
                INSERT INTO case_fields (id, case_id, workspace_id)
                VALUES
                    (gen_random_uuid(), :case1, :ws1_id),
                    (gen_random_uuid(), :case2, :ws1_id),
                    (gen_random_uuid(), :case3, :ws2_id)
                """
            ),
            {
                "case1": case1_ws1_id,
                "case2": case2_ws1_id,
                "ws1_id": workspace1_id,
                "case3": case1_ws2_id,
                "ws2_id": workspace2_id,
            },
        )

    engine.dispose()

    return {
        "workspace_ids": [workspace1_id, workspace2_id, workspace3_id],
        "case_ids": [case1_ws1_id, case2_ws1_id, case1_ws2_id],
    }


class TestCaseFieldsMigrationUpgrade:
    """Tests for the upgrade path (b4d8b2f2c9dd)."""

    def test_upgrade_creates_workspace_schemas(
        self, test_db: str, sample_data: dict[str, list[uuid.UUID]]
    ) -> None:
        """Test that schemas are created for each workspace."""
        workspace_ids = sample_data["workspace_ids"]

        # Run the actual alembic migration
        _run_alembic_upgrade(test_db)

        # Verify schemas exist for workspaces with data
        engine = create_engine(test_db)
        try:
            with engine.begin() as conn:
                # First two workspaces have data, third is empty
                for workspace_id in workspace_ids[:2]:
                    schema_name = _workspace_schema(workspace_id)
                    result = conn.execute(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name = :schema)"
                        ),
                        {"schema": schema_name},
                    )
                    assert result.scalar_one(), f"Schema {schema_name} should exist"

                # Third workspace has no data, so schema should also be created
                schema_name = _workspace_schema(workspace_ids[2])
                result = conn.execute(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name = :schema)"
                    ),
                    {"schema": schema_name},
                )
                assert result.scalar_one(), (
                    f"Schema {schema_name} should exist for empty workspace"
                )
        finally:
            engine.dispose()

    def test_upgrade_creates_tables_in_workspace_schemas(
        self, test_db: str, sample_data: dict[str, list[uuid.UUID]]
    ) -> None:
        """Test that case_fields tables are created in workspace schemas."""
        workspace_ids = sample_data["workspace_ids"]

        # Run the actual alembic migration
        _run_alembic_upgrade(test_db)

        # Verify tables exist and have correct structure
        engine = create_engine(test_db)
        try:
            with engine.begin() as conn:
                # Check all workspaces (migration creates tables for all workspaces)
                for workspace_id in workspace_ids:
                    schema_name = _workspace_schema(workspace_id)

                    result = conn.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = :schema AND table_name = :table
                            ORDER BY ordinal_position
                            """
                        ),
                        {"schema": schema_name, "table": TABLE_NAME},
                    )
                    columns = {row[0] for row in result.fetchall()}

                    assert "id" in columns, f"id column missing in {schema_name}"
                    assert "case_id" in columns, (
                        f"case_id column missing in {schema_name}"
                    )
                    assert "created_at" in columns, (
                        f"created_at column missing in {schema_name}"
                    )
                    assert "updated_at" in columns, (
                        f"updated_at column missing in {schema_name}"
                    )
                    assert "workspace_id" not in columns, (
                        f"workspace_id should NOT be in workspace table {schema_name}"
                    )
        finally:
            engine.dispose()

    def test_upgrade_applies_constraints(
        self, test_db: str, sample_data: dict[str, list[uuid.UUID]]
    ) -> None:
        """Upgrade should preserve FK and uniqueness constraints in workspace tables."""
        workspace_ids = sample_data["workspace_ids"]

        _run_alembic_upgrade(test_db)

        engine = create_engine(test_db)
        try:
            with engine.begin() as conn:
                for workspace_id in workspace_ids:
                    schema_name = _workspace_schema(workspace_id)
                    assert _has_fk_to_cases(conn, schema_name), (
                        f"{schema_name}.{TABLE_NAME} should keep FK to cases(id)"
                    )
                    assert _has_unique_case_id(conn, schema_name), (
                        f"{schema_name}.{TABLE_NAME} should enforce unique case_id"
                    )
        finally:
            engine.dispose()

    def test_upgrade_migrates_data(
        self, test_db: str, sample_data: dict[str, list[uuid.UUID]]
    ) -> None:
        """Test that data is migrated to workspace schemas."""
        workspace_ids = sample_data["workspace_ids"]

        # Run the actual alembic migration
        _run_alembic_upgrade(test_db)

        # Verify data was migrated correctly
        engine = create_engine(test_db)
        try:
            with engine.begin() as conn:
                # Check workspaces with data (first two)
                for workspace_id in workspace_ids[:2]:
                    schema_name = _workspace_schema(workspace_id)

                    # Count rows in workspace table
                    ws_result = conn.execute(
                        text(f'SELECT COUNT(*) FROM "{schema_name}".{TABLE_NAME}')
                    )
                    ws_count = ws_result.scalar_one()

                    # Count rows in original public table for this workspace
                    public_result = conn.execute(
                        text(
                            f"SELECT COUNT(*) FROM {PUBLIC_SCHEMA}.{TABLE_NAME} WHERE workspace_id = :ws_id"
                        ),
                        {"ws_id": workspace_id},
                    )
                    public_count = public_result.scalar_one()

                    assert ws_count == public_count, (
                        f"Data migration mismatch for {workspace_id}: "
                        f"workspace table has {ws_count} rows, "
                        f"public table has {public_count} rows"
                    )
                    assert ws_count > 0, f"No data migrated for {workspace_id}"

                # Check empty workspace (third one)
                schema_name = _workspace_schema(workspace_ids[2])
                ws_result = conn.execute(
                    text(f'SELECT COUNT(*) FROM "{schema_name}".{TABLE_NAME}')
                )
                ws_count = ws_result.scalar_one()
                assert ws_count == 0, (
                    f"Empty workspace should have 0 rows, has {ws_count}"
                )
        finally:
            engine.dispose()

    def test_upgrade_migrates_custom_columns(self, test_db: str) -> None:
        """Test that custom columns are migrated to workspace schemas."""
        # Create a workspace and add custom columns to the public case_fields table
        workspace_id = uuid.uuid4()
        case_id = uuid.uuid4()

        engine = create_engine(test_db)
        try:
            with engine.begin() as conn:
                # Add workspace
                conn.execute(
                    text(
                        """
                        INSERT INTO workspace (id, name, owner_id, settings)
                        VALUES (:id, :name, :owner_id, :settings)
                        """
                    ),
                    {
                        "id": workspace_id,
                        "name": "test-workspace-custom",
                        "owner_id": TEST_ORG_ID,
                        "settings": "{}",
                    },
                )

                # Add case
                conn.execute(
                    text(
                        """
                        INSERT INTO cases (id, workspace_id, summary, description, status, priority, severity)
                        VALUES (:id, :ws_id, 'Test Case', 'Desc', 'NEW', 'MEDIUM', 'LOW')
                        """
                    ),
                    {"id": case_id, "ws_id": workspace_id},
                )

                # Add custom columns to case_fields table
                conn.execute(
                    text(
                        """
                        ALTER TABLE case_fields
                        ADD COLUMN custom_field1 TEXT,
                        ADD COLUMN custom_field2 INTEGER,
                        ADD COLUMN custom_field3 BOOLEAN
                        """
                    )
                )

                # Insert data with custom columns
                conn.execute(
                    text(
                        """
                        INSERT INTO case_fields (id, case_id, workspace_id, custom_field1, custom_field2, custom_field3)
                        VALUES (gen_random_uuid(), :case_id, :ws_id, 'test value', 42, true)
                        """
                    ),
                    {"case_id": case_id, "ws_id": workspace_id},
                )

            # Re-stamp the database since we modified the schema
            _run_alembic_stamp(test_db, PREVIOUS_REVISION)

            # Run the migration
            _run_alembic_upgrade(test_db)

            # Verify custom columns were migrated
            with engine.begin() as conn:
                schema_name = _workspace_schema(workspace_id)

                # Check that custom columns exist in workspace table
                result = conn.execute(
                    text(
                        """
                        SELECT column_name, data_type, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = :schema AND table_name = :table
                        ORDER BY ordinal_position
                        """
                    ),
                    {"schema": schema_name, "table": TABLE_NAME},
                )
                column_rows = result.fetchall()
                columns = {row[0] for row in column_rows}

                assert "custom_field1" in columns, "custom_field1 should be migrated"
                assert "custom_field2" in columns, "custom_field2 should be migrated"
                assert "custom_field3" in columns, "custom_field3 should be migrated"

                column_info = {
                    row[0]: {"data_type": row[1], "is_nullable": row[2] == "YES"}
                    for row in column_rows
                }

                # Validate types and nullability are preserved
                assert column_info["custom_field1"]["data_type"] == "text"
                assert column_info["custom_field1"]["is_nullable"] is True
                assert column_info["custom_field2"]["data_type"] == "integer"
                assert column_info["custom_field2"]["is_nullable"] is True
                assert column_info["custom_field3"]["data_type"] == "boolean"
                assert column_info["custom_field3"]["is_nullable"] is True

                # Verify data was migrated with custom column values
                result = conn.execute(
                    text(
                        f"""
                        SELECT custom_field1, custom_field2, custom_field3
                        FROM "{schema_name}".{TABLE_NAME}
                        WHERE case_id = :case_id
                        """
                    ),
                    {"case_id": case_id},
                )
                row = result.fetchone()
                assert row is not None, "Data should be migrated"
                assert row[0] == "test value", "custom_field1 value should be preserved"
                assert row[1] == 42, "custom_field2 value should be preserved"
                assert row[2] is True, "custom_field3 value should be preserved"
                # Public table should retain FK metadata to cases table
                assert _has_fk_to_cases(conn, PUBLIC_SCHEMA), (
                    "Public case_fields table should retain FK from case_id to cases(id)"
                )
        finally:
            engine.dispose()


class TestCaseFieldsMigrationDowngrade:
    """Tests for the downgrade path."""

    def test_downgrade_restores_data(
        self, test_db: str, sample_data: dict[str, list[uuid.UUID]]
    ) -> None:
        """Test that downgrade moves data back to public schema."""
        workspace_ids = sample_data["workspace_ids"]

        # First run upgrade to create workspace schemas and migrate data
        _run_alembic_upgrade(test_db)

        engine = create_engine(test_db)
        try:
            with engine.begin() as conn:
                # Store the original counts before downgrade
                original_counts = {}
                for workspace_id in workspace_ids[:2]:
                    schema_name = _workspace_schema(workspace_id)
                    result = conn.execute(
                        text(f'SELECT COUNT(*) FROM "{schema_name}".{TABLE_NAME}')
                    )
                    original_counts[workspace_id] = result.scalar_one()

            # Now run downgrade
            _run_alembic_downgrade(test_db)

            with engine.begin() as conn:
                # Verify data is back in public schema
                for workspace_id in workspace_ids[:2]:
                    result = conn.execute(
                        text(
                            f"SELECT COUNT(*) FROM {PUBLIC_SCHEMA}.{TABLE_NAME} WHERE workspace_id = :ws_id"
                        ),
                        {"ws_id": workspace_id},
                    )
                    public_count = result.scalar_one()
                    expected_count = original_counts[workspace_id]

                    assert public_count == expected_count, (
                        f"Data restoration mismatch for {workspace_id}: "
                        f"expected {expected_count} rows, got {public_count} rows"
                    )
        finally:
            engine.dispose()

    def test_downgrade_drops_schemas(
        self, test_db: str, sample_data: dict[str, list[uuid.UUID]]
    ) -> None:
        """Test that downgrade drops workspace schemas."""
        workspace_ids = sample_data["workspace_ids"]

        # First run upgrade to create workspace schemas
        _run_alembic_upgrade(test_db)

        engine = create_engine(test_db)
        try:
            with engine.begin() as conn:
                # Verify schemas exist after upgrade
                for workspace_id in workspace_ids:
                    schema_name = _workspace_schema(workspace_id)
                    result = conn.execute(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name = :schema)"
                        ),
                        {"schema": schema_name},
                    )
                    assert result.scalar_one(), (
                        f"Schema {schema_name} should exist after upgrade"
                    )

            # Run downgrade
            _run_alembic_downgrade(test_db)

            with engine.begin() as conn:
                # Verify schemas are dropped after downgrade
                for workspace_id in workspace_ids:
                    schema_name = _workspace_schema(workspace_id)
                    result = conn.execute(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name = :schema)"
                        ),
                        {"schema": schema_name},
                    )
                    assert not result.scalar_one(), (
                        f"Schema {schema_name} should be dropped after downgrade"
                    )
        finally:
            engine.dispose()

    def test_downgrade_restores_custom_columns_and_values(self, test_db: str) -> None:
        """Downgrade should recreate custom columns in public table with data restored."""
        workspace_id = uuid.uuid4()
        case_id = uuid.uuid4()

        engine = create_engine(test_db)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO workspace (id, name, owner_id, settings)
                        VALUES (:id, :name, :owner_id, :settings)
                        """
                    ),
                    {
                        "id": workspace_id,
                        "name": "downgrade-custom",
                        "owner_id": TEST_ORG_ID,
                        "settings": "{}",
                    },
                )

                conn.execute(
                    text(
                        """
                        INSERT INTO cases (id, workspace_id, summary, description, status, priority, severity)
                        VALUES (:id, :ws_id, 'Case', 'Desc', 'NEW', 'LOW', 'LOW')
                        """
                    ),
                    {"id": case_id, "ws_id": workspace_id},
                )

                conn.execute(
                    text(
                        """
                        ALTER TABLE case_fields
                        ADD COLUMN custom_field_text TEXT,
                        ADD COLUMN custom_field_int INTEGER
                        """
                    )
                )

                conn.execute(
                    text(
                        """
                        INSERT INTO case_fields (id, case_id, workspace_id, custom_field_text, custom_field_int)
                        VALUES (gen_random_uuid(), :case_id, :ws_id, 'abc', 7)
                        """
                    ),
                    {"case_id": case_id, "ws_id": workspace_id},
                )

            _run_alembic_stamp(test_db, PREVIOUS_REVISION)
            _run_alembic_upgrade(test_db)
            _run_alembic_downgrade(test_db)

            with engine.begin() as conn:
                public_columns = conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = :schema AND table_name = :table
                        """
                    ),
                    {"schema": PUBLIC_SCHEMA, "table": TABLE_NAME},
                ).fetchall()
                public_column_names = {row[0] for row in public_columns}

                assert "custom_field_text" in public_column_names
                assert "custom_field_int" in public_column_names

                row = conn.execute(
                    text(
                        f"""
                        SELECT custom_field_text, custom_field_int, workspace_id
                        FROM {PUBLIC_SCHEMA}.{TABLE_NAME}
                        WHERE case_id = :case_id
                        """
                    ),
                    {"case_id": case_id},
                ).fetchone()

                assert row is not None, "Row should be restored to public table"
                assert row[0] == "abc"
                assert row[1] == 7
                assert row[2] == workspace_id
        finally:
            engine.dispose()
