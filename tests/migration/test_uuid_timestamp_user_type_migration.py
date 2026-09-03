"""Tests for migrating legacy UUID/TIMESTAMP user-defined types."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Generator
from datetime import UTC, datetime

import orjson
import pytest
from sqlalchemy import create_engine, text

from tests.database import TEST_DB_CONFIG
from tracecat.identifiers.workflow import WorkspaceUUID

MIGRATION_REVISION = "7e1a4d9c2b6f"
PREVIOUS_REVISION = "bf38f2aa1c77"


def _workspace_schema(prefix: str, workspace_id: uuid.UUID) -> str:
    return f"{prefix}{WorkspaceUUID.new(workspace_id).short()}"


def _run_alembic(db_url: str, command: str, revision: str) -> None:
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = db_url
    result = subprocess.run(
        ["uv", "run", "alembic", command, revision],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Alembic {command} failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


@pytest.fixture(scope="function")
def test_db() -> Generator[str, None, None]:
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT"
    )
    test_db_name = f"test_uuid_timestamp_migration_{uuid.uuid4().hex[:8]}"
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

        test_url_sync = TEST_DB_CONFIG.test_url_sync.replace(
            TEST_DB_CONFIG.test_db_name, test_db_name
        )

        engine = create_engine(test_url_sync)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE workspace (id UUID PRIMARY KEY)"))
            conn.execute(
                text(
                    """
                    CREATE TABLE tables (
                        id UUID PRIMARY KEY,
                        workspace_id UUID NOT NULL,
                        name TEXT NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE table_column (
                        id UUID PRIMARY KEY,
                        table_id UUID NOT NULL,
                        name TEXT NOT NULL,
                        type TEXT NOT NULL,
                        "default" JSONB NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE case_field (
                        id UUID PRIMARY KEY,
                        workspace_id UUID NOT NULL,
                        schema JSONB NOT NULL DEFAULT '{}'::jsonb
                    )
                    """
                )
            )
        engine.dispose()

        _run_alembic(test_url_sync, "stamp", PREVIOUS_REVISION)
        yield test_url_sync
    finally:
        with default_engine.begin() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}"'))
        default_engine.dispose()


@pytest.fixture
def seeded_db(test_db: str) -> dict[str, str]:
    engine = create_engine(test_db)
    workspace_id = uuid.uuid4()
    table_id = uuid.uuid4()
    case_field_id = uuid.uuid4()
    table_schema = _workspace_schema("tables_", workspace_id)
    custom_field_schema = _workspace_schema("custom_fields_", workspace_id)

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO workspace (id) VALUES (:workspace_id)"),
            {"workspace_id": workspace_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO tables (id, workspace_id, name)
                VALUES (:table_id, :workspace_id, 'assets')
                """
            ),
            {"table_id": table_id, "workspace_id": workspace_id},
        )
        conn.execute(text(f'CREATE SCHEMA "{table_schema}"'))
        conn.execute(text(f'CREATE SCHEMA "{custom_field_schema}"'))
        conn.execute(
            text(
                f"""
                CREATE TABLE "{table_schema}"."assets" (
                    id UUID PRIMARY KEY,
                    legacy_uuid UUID DEFAULT '00000000-0000-0000-0000-000000000000'::uuid,
                    legacy_timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT '2024-01-02 03:04:05'::timestamp without time zone,
                    untouched_text TEXT DEFAULT 'keep'
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                INSERT INTO "{table_schema}"."assets" (
                    id,
                    legacy_uuid,
                    legacy_timestamp,
                    untouched_text
                )
                VALUES (
                    :row_id,
                    '11111111-1111-1111-1111-111111111111'::uuid,
                    '2024-02-03 04:05:06'::timestamp without time zone,
                    'keep'
                )
                """
            ),
            {"row_id": uuid.uuid4()},
        )
        conn.execute(
            text(
                """
                INSERT INTO table_column (id, table_id, name, type, "default")
                VALUES
                    (:uuid_column_id, :table_id, 'legacy_uuid', 'UUID', '\"00000000-0000-0000-0000-000000000000\"'::jsonb),
                    (:timestamp_column_id, :table_id, 'legacy_timestamp', 'TIMESTAMP', '\"2024-01-02T03:04:05\"'::jsonb),
                    (:text_column_id, :table_id, 'untouched_text', 'TEXT', '\"keep\"'::jsonb)
                """
            ),
            {
                "uuid_column_id": uuid.uuid4(),
                "timestamp_column_id": uuid.uuid4(),
                "text_column_id": uuid.uuid4(),
                "table_id": table_id,
            },
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE "{custom_field_schema}"."case_fields" (
                    id UUID PRIMARY KEY,
                    case_id UUID NOT NULL UNIQUE,
                    legacy_uuid UUID DEFAULT '22222222-2222-2222-2222-222222222222'::uuid,
                    legacy_timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT '2024-04-05 06:07:08'::timestamp without time zone
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                INSERT INTO "{custom_field_schema}"."case_fields" (
                    id,
                    case_id,
                    legacy_uuid,
                    legacy_timestamp
                )
                VALUES (
                    :row_id,
                    :case_id,
                    '33333333-3333-3333-3333-333333333333'::uuid,
                    '2024-05-06 07:08:09'::timestamp without time zone
                )
                """
            ),
            {"row_id": uuid.uuid4(), "case_id": uuid.uuid4()},
        )
        conn.execute(
            text(
                """
                INSERT INTO case_field (id, workspace_id, schema)
                VALUES (
                    :case_field_id,
                    :workspace_id,
                    CAST(:schema AS JSONB)
                )
                """
            ),
            {
                "case_field_id": case_field_id,
                "workspace_id": workspace_id,
                "schema": orjson.dumps(
                    {
                        "legacy_uuid": {"type": "UUID"},
                        "legacy_timestamp": {
                            "type": "TIMESTAMP",
                            "required_on_closure": True,
                        },
                        "untouched_text": {"type": "TEXT"},
                    }
                ).decode(),
            },
        )

    engine.dispose()
    return {
        "db_url": test_db,
        "workspace_id": str(workspace_id),
        "table_schema": table_schema,
        "custom_field_schema": custom_field_schema,
    }


def test_upgrade_migrates_uuid_and_timestamp_user_types(
    seeded_db: dict[str, str],
) -> None:
    _run_alembic(seeded_db["db_url"], "upgrade", MIGRATION_REVISION)

    engine = create_engine(seeded_db["db_url"])
    with engine.begin() as conn:
        table_types = conn.execute(
            text(
                """
                SELECT name, type, "default"
                FROM table_column
                ORDER BY name
                """
            )
        ).mappings()
        table_type_map = {row["name"]: row for row in table_types}
        assert table_type_map["legacy_uuid"]["type"] == "TEXT"
        assert table_type_map["legacy_uuid"]["default"] == (
            "00000000-0000-0000-0000-000000000000"
        )
        assert table_type_map["legacy_timestamp"]["type"] == "TIMESTAMPTZ"
        assert table_type_map["legacy_timestamp"]["default"] == (
            "2024-01-02T03:04:05+00:00"
        )
        assert table_type_map["untouched_text"]["type"] == "TEXT"

        lookup_column_types = conn.execute(
            text(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = :schema_name
                  AND table_name = 'assets'
                  AND column_name IN ('legacy_uuid', 'legacy_timestamp')
                ORDER BY column_name
                """
            ),
            {"schema_name": seeded_db["table_schema"]},
        ).mappings()
        lookup_type_map = {
            row["column_name"]: row["data_type"] for row in lookup_column_types
        }
        assert lookup_type_map["legacy_uuid"] == "text"
        assert lookup_type_map["legacy_timestamp"] == "timestamp with time zone"

        lookup_row = (
            conn.execute(
                text(
                    f"""
                SELECT legacy_uuid, legacy_timestamp
                FROM "{seeded_db["table_schema"]}"."assets"
                """
                )
            )
            .mappings()
            .one()
        )
        assert lookup_row["legacy_uuid"] == "11111111-1111-1111-1111-111111111111"
        assert lookup_row["legacy_timestamp"] == datetime(
            2024, 2, 3, 4, 5, 6, tzinfo=UTC
        )

        lookup_default_row = (
            conn.execute(
                text(
                    f"""
                INSERT INTO "{seeded_db["table_schema"]}"."assets" (id)
                VALUES (:row_id)
                RETURNING legacy_uuid, legacy_timestamp
                """
                ),
                {"row_id": uuid.uuid4()},
            )
            .mappings()
            .one()
        )
        assert lookup_default_row["legacy_uuid"] == (
            "00000000-0000-0000-0000-000000000000"
        )
        assert lookup_default_row["legacy_timestamp"] == datetime(
            2024, 1, 2, 3, 4, 5, tzinfo=UTC
        )

        case_field_schema = conn.execute(
            text("SELECT schema FROM case_field")
        ).scalar_one()
        assert case_field_schema["legacy_uuid"]["type"] == "TEXT"
        assert case_field_schema["legacy_timestamp"]["type"] == "TIMESTAMPTZ"
        assert case_field_schema["legacy_timestamp"]["required_on_closure"] is True
        assert case_field_schema["untouched_text"]["type"] == "TEXT"

        case_field_column_types = conn.execute(
            text(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = :schema_name
                  AND table_name = 'case_fields'
                  AND column_name IN ('legacy_uuid', 'legacy_timestamp')
                ORDER BY column_name
                """
            ),
            {"schema_name": seeded_db["custom_field_schema"]},
        ).mappings()
        case_field_type_map = {
            row["column_name"]: row["data_type"] for row in case_field_column_types
        }
        assert case_field_type_map["legacy_uuid"] == "text"
        assert case_field_type_map["legacy_timestamp"] == "timestamp with time zone"

        case_field_row = (
            conn.execute(
                text(
                    f"""
                SELECT legacy_uuid, legacy_timestamp
                FROM "{seeded_db["custom_field_schema"]}"."case_fields"
                """
                )
            )
            .mappings()
            .one()
        )
        assert case_field_row["legacy_uuid"] == ("33333333-3333-3333-3333-333333333333")
        assert case_field_row["legacy_timestamp"] == datetime(
            2024, 5, 6, 7, 8, 9, tzinfo=UTC
        )

        case_field_default_row = (
            conn.execute(
                text(
                    f"""
                INSERT INTO "{seeded_db["custom_field_schema"]}"."case_fields" (
                    id,
                    case_id
                )
                VALUES (:row_id, :case_id)
                RETURNING legacy_uuid, legacy_timestamp
                """
                ),
                {"row_id": uuid.uuid4(), "case_id": uuid.uuid4()},
            )
            .mappings()
            .one()
        )
        assert case_field_default_row["legacy_uuid"] == (
            "22222222-2222-2222-2222-222222222222"
        )
        assert case_field_default_row["legacy_timestamp"] == datetime(
            2024, 4, 5, 6, 7, 8, tzinfo=UTC
        )

    engine.dispose()
