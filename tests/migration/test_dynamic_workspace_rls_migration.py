"""Tests for dynamic workspace RLS migration.

This module exercises the unreleased b5fc4168fe22 migration by creating
legacy dynamic workspace tables without the internal tenant column,
stamping the database to the previous revision, and running the actual
alembic upgrade/downgrade commands.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text

from tests.database import TEST_DB_CONFIG
from tracecat.identifiers.workflow import WorkspaceUUID

MIGRATION_REVISION = "b5fc4168fe22"
# Immediate down_revision for b5fc4168fe22.
PREVIOUS_REVISION = "3431033d29fd"
TABLES_PREFIX = "tables_"
CUSTOM_FIELDS_PREFIX = "custom_fields_"
TABLES_TABLE = "alerts"
CUSTOM_FIELDS_TABLE = "case_fields"
INTERNAL_TENANT_COLUMN = "__tc_workspace_id"
LEGACY_TENANT_COLUMN = "migrated_tc_workspace_id"
LEGACY_INTERNAL_COLUMN = "__tc_shadow"
MIGRATED_INTERNAL_COLUMN = "migrated_tc_shadow"
DYNAMIC_WORKSPACE_RLS_POLICY = "rls_policy_dynamic_workspace"


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for migration tests."""
    yield


def _workspace_schema(prefix: str, workspace_id: uuid.UUID) -> str:
    return f"{prefix}{WorkspaceUUID.new(workspace_id).short()}"


def _run_alembic_upgrade(db_url: str, revision: str = MIGRATION_REVISION) -> None:
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


def _dynamic_table_access(
    conn,
    *,
    role_name: str,
    schema_name: str,
    table_name: str,
    workspace_id: uuid.UUID,
    bypass: bool = False,
) -> list[uuid.UUID]:
    conn.execute(text(f'SET ROLE "{role_name}"'))
    try:
        conn.execute(
            text(
                """
                SELECT
                    set_config('app.current_workspace_id', :workspace_id, true),
                    set_config('app.rls_bypass', :bypass, true)
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "bypass": "on" if bypass else "off",
            },
        )
        result = conn.execute(
            text(
                f'''
                SELECT id
                FROM "{schema_name}"."{table_name}"
                ORDER BY id
                '''
            )
        )
        return [row[0] for row in result.fetchall()]
    finally:
        conn.execute(text("RESET ROLE"))


def _get_column_comment(
    conn, *, schema_name: str, table_name: str, column_name: str
) -> str | None:
    """Fetch a column comment from PostgreSQL system catalogs."""
    return conn.execute(
        text(
            """
            SELECT pg_catalog.col_description(cls.oid, attr.attnum)
            FROM pg_catalog.pg_class AS cls
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
            JOIN pg_catalog.pg_attribute AS attr ON attr.attrelid = cls.oid
            WHERE ns.nspname = :schema_name
              AND cls.relname = :table_name
              AND attr.attname = :column_name
              AND attr.attnum > 0
              AND NOT attr.attisdropped
            """
        ),
        {
            "schema_name": schema_name,
            "table_name": table_name,
            "column_name": column_name,
        },
    ).scalar_one_or_none()


def _seed_legacy_internal_metadata(
    conn, *, workspace_id: uuid.UUID, column_name: str
) -> None:
    """Create minimal metadata tables used by legacy internal rename helpers."""
    table_metadata_id = uuid.uuid4()
    table_column_id = uuid.uuid4()
    case_field_id = uuid.uuid4()

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
                name TEXT NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE case_field (
                id UUID PRIMARY KEY,
                workspace_id UUID NOT NULL UNIQUE,
                schema JSONB NOT NULL
            )
            """
        )
    )

    conn.execute(
        text(
            """
            INSERT INTO tables (id, workspace_id, name)
            VALUES (:id, :workspace_id, :table_name)
            """
        ),
        {
            "id": table_metadata_id,
            "workspace_id": workspace_id,
            "table_name": TABLES_TABLE,
        },
    )
    conn.execute(
        text(
            """
            INSERT INTO table_column (id, table_id, name)
            VALUES (:id, :table_id, :column_name)
            """
        ),
        {
            "id": table_column_id,
            "table_id": table_metadata_id,
            "column_name": column_name,
        },
    )
    conn.execute(
        text(
            """
            INSERT INTO case_field (id, workspace_id, schema)
            VALUES (:id, :workspace_id, CAST(:schema AS JSONB))
            """
        ),
        {
            "id": case_field_id,
            "workspace_id": workspace_id,
            "schema": json.dumps(
                {
                    column_name: {"type": "text"},
                    "field_text": {"type": "text"},
                }
            ),
        },
    )


@pytest.fixture(scope="function")
def test_db():
    """Create a test database with legacy dynamic workspace tables."""
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT"
    )
    test_db_name = f"test_dynamic_rls_{uuid.uuid4().hex[:8]}"
    reader_role = f"rls_reader_{uuid.uuid4().hex[:8]}"

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
            conn.execute(text(f'CREATE ROLE "{reader_role}"'))

        test_url_sync = TEST_DB_CONFIG.test_url_sync.replace(
            TEST_DB_CONFIG.test_db_name, test_db_name
        )
        engine = create_engine(test_url_sync)

        workspace_ids = [uuid.uuid4(), uuid.uuid4()]
        row_ids: dict[str, dict[uuid.UUID, uuid.UUID]] = {
            TABLES_TABLE: {},
            CUSTOM_FIELDS_TABLE: {},
        }

        with engine.begin() as conn:
            for workspace_id in workspace_ids:
                tables_schema = _workspace_schema(TABLES_PREFIX, workspace_id)
                custom_fields_schema = _workspace_schema(
                    CUSTOM_FIELDS_PREFIX, workspace_id
                )

                conn.execute(text(f'CREATE SCHEMA "{tables_schema}"'))
                conn.execute(text(f'CREATE SCHEMA "{custom_fields_schema}"'))

                conn.execute(
                    text(
                        f'''
                        CREATE TABLE "{tables_schema}"."{TABLES_TABLE}" (
                            id UUID PRIMARY KEY,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            name TEXT NOT NULL
                        )
                        '''
                    )
                )
                conn.execute(
                    text(
                        f'''
                        CREATE TABLE "{custom_fields_schema}"."{CUSTOM_FIELDS_TABLE}" (
                            id UUID PRIMARY KEY,
                            case_id UUID NOT NULL UNIQUE,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            field_text TEXT
                        )
                        '''
                    )
                )

                table_row_id = uuid.uuid4()
                case_fields_row_id = uuid.uuid4()
                row_ids[TABLES_TABLE][workspace_id] = table_row_id
                row_ids[CUSTOM_FIELDS_TABLE][workspace_id] = case_fields_row_id

                conn.execute(
                    text(
                        f'''
                        INSERT INTO "{tables_schema}"."{TABLES_TABLE}" (id, name)
                        VALUES (:row_id, :name)
                        '''
                    ),
                    {"row_id": table_row_id, "name": f"alert-{workspace_id.hex[:6]}"},
                )
                conn.execute(
                    text(
                        f'''
                        INSERT INTO "{custom_fields_schema}"."{CUSTOM_FIELDS_TABLE}" (
                            id, case_id, field_text
                        )
                        VALUES (:row_id, :case_id, :field_text)
                        '''
                    ),
                    {
                        "row_id": case_fields_row_id,
                        "case_id": uuid.uuid4(),
                        "field_text": f"field-{workspace_id.hex[:6]}",
                    },
                )

                conn.execute(
                    text(f'GRANT USAGE ON SCHEMA "{tables_schema}" TO "{reader_role}"')
                )
                conn.execute(
                    text(
                        f'GRANT SELECT ON TABLE "{tables_schema}"."{TABLES_TABLE}" TO "{reader_role}"'
                    )
                )
                conn.execute(
                    text(
                        f'GRANT USAGE ON SCHEMA "{custom_fields_schema}" TO "{reader_role}"'
                    )
                )
                conn.execute(
                    text(
                        f'GRANT SELECT ON TABLE "{custom_fields_schema}"."{CUSTOM_FIELDS_TABLE}" TO "{reader_role}"'
                    )
                )

        engine.dispose()
        _run_alembic_stamp(test_url_sync, PREVIOUS_REVISION)

        yield {
            "db_url": test_url_sync,
            "reader_role": reader_role,
            "workspace_ids": workspace_ids,
            "row_ids": row_ids,
        }
    finally:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}"'))
            conn.execute(text(f'DROP ROLE IF EXISTS "{reader_role}"'))
        default_engine.dispose()


class TestDynamicWorkspaceRlsMigration:
    def test_upgrade_renames_legacy_exact_tenant_column_collision(
        self, test_db
    ) -> None:
        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                workspace_id = test_db["workspace_ids"][0]
                for prefix, table_name in (
                    (TABLES_PREFIX, TABLES_TABLE),
                    (CUSTOM_FIELDS_PREFIX, CUSTOM_FIELDS_TABLE),
                ):
                    schema_name = _workspace_schema(prefix, workspace_id)
                    conn.execute(
                        text(
                            f'''
                            ALTER TABLE "{schema_name}"."{table_name}"
                            ADD COLUMN "{INTERNAL_TENANT_COLUMN}" TEXT
                            '''
                        )
                    )
                    conn.execute(
                        text(
                            f'''
                            UPDATE "{schema_name}"."{table_name}"
                            SET "{INTERNAL_TENANT_COLUMN}" = :legacy_value
                            '''
                        ),
                        {"legacy_value": f"legacy-{table_name}"},
                    )
        finally:
            engine.dispose()

        _run_alembic_upgrade(test_db["db_url"])

        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                workspace_id = test_db["workspace_ids"][0]
                for prefix, table_name in (
                    (TABLES_PREFIX, TABLES_TABLE),
                    (CUSTOM_FIELDS_PREFIX, CUSTOM_FIELDS_TABLE),
                ):
                    schema_name = _workspace_schema(prefix, workspace_id)
                    columns = conn.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = :schema_name
                              AND table_name = :table_name
                            """
                        ),
                        {
                            "schema_name": schema_name,
                            "table_name": table_name,
                        },
                    ).fetchall()
                    column_names = {row[0] for row in columns}
                    assert INTERNAL_TENANT_COLUMN in column_names
                    assert LEGACY_TENANT_COLUMN in column_names

                    tenant_values = conn.execute(
                        text(
                            f'''
                            SELECT "{INTERNAL_TENANT_COLUMN}", "{LEGACY_TENANT_COLUMN}"
                            FROM "{schema_name}"."{table_name}"
                            '''
                        )
                    ).one()
                    assert tenant_values[0] == workspace_id
                    assert tenant_values[1] == f"legacy-{table_name}"
        finally:
            engine.dispose()

    def test_upgrade_renames_legacy_internal_namespace_columns_and_metadata(
        self, test_db
    ) -> None:
        workspace_id = test_db["workspace_ids"][0]

        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                _seed_legacy_internal_metadata(
                    conn,
                    workspace_id=workspace_id,
                    column_name=LEGACY_INTERNAL_COLUMN,
                )
                for prefix, table_name in (
                    (TABLES_PREFIX, TABLES_TABLE),
                    (CUSTOM_FIELDS_PREFIX, CUSTOM_FIELDS_TABLE),
                ):
                    schema_name = _workspace_schema(prefix, workspace_id)
                    conn.execute(
                        text(
                            f'''
                            ALTER TABLE "{schema_name}"."{table_name}"
                            ADD COLUMN "{LEGACY_INTERNAL_COLUMN}" TEXT
                            '''
                        )
                    )
                    conn.execute(
                        text(
                            f'''
                            UPDATE "{schema_name}"."{table_name}"
                            SET "{LEGACY_INTERNAL_COLUMN}" = :legacy_value
                            '''
                        ),
                        {"legacy_value": f"legacy-{table_name}"},
                    )
        finally:
            engine.dispose()

        _run_alembic_upgrade(test_db["db_url"])

        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                for prefix, table_name in (
                    (TABLES_PREFIX, TABLES_TABLE),
                    (CUSTOM_FIELDS_PREFIX, CUSTOM_FIELDS_TABLE),
                ):
                    schema_name = _workspace_schema(prefix, workspace_id)
                    columns = conn.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = :schema_name
                              AND table_name = :table_name
                            """
                        ),
                        {"schema_name": schema_name, "table_name": table_name},
                    ).fetchall()
                    column_names = {row[0] for row in columns}
                    assert LEGACY_INTERNAL_COLUMN not in column_names
                    assert MIGRATED_INTERNAL_COLUMN in column_names

                    legacy_value = conn.execute(
                        text(
                            f'''
                            SELECT "{MIGRATED_INTERNAL_COLUMN}"
                            FROM "{schema_name}"."{table_name}"
                            '''
                        )
                    ).scalar_one()
                    assert legacy_value == f"legacy-{table_name}"

                table_column_names = conn.execute(
                    text(
                        """
                        SELECT tc.name
                        FROM table_column AS tc
                        JOIN tables AS t ON tc.table_id = t.id
                        WHERE t.workspace_id = :workspace_id
                          AND t.name = :table_name
                        """
                    ),
                    {"workspace_id": workspace_id, "table_name": TABLES_TABLE},
                ).fetchall()
                assert [row[0] for row in table_column_names] == [
                    MIGRATED_INTERNAL_COLUMN
                ]

                case_field_schema = conn.execute(
                    text(
                        """
                        SELECT schema
                        FROM case_field
                        WHERE workspace_id = :workspace_id
                        """
                    ),
                    {"workspace_id": workspace_id},
                ).scalar_one()
                assert MIGRATED_INTERNAL_COLUMN in case_field_schema
                assert LEGACY_INTERNAL_COLUMN not in case_field_schema
        finally:
            engine.dispose()

    def test_downgrade_restores_legacy_collision_column_and_metadata(
        self, test_db
    ) -> None:
        legacy_comments = {
            TABLES_TABLE: "alerts legacy comment",
            CUSTOM_FIELDS_TABLE: "case fields legacy comment",
        }
        workspace_id = test_db["workspace_ids"][0]

        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                _seed_legacy_internal_metadata(
                    conn,
                    workspace_id=workspace_id,
                    column_name=INTERNAL_TENANT_COLUMN,
                )
                for prefix, table_name in (
                    (TABLES_PREFIX, TABLES_TABLE),
                    (CUSTOM_FIELDS_PREFIX, CUSTOM_FIELDS_TABLE),
                ):
                    schema_name = _workspace_schema(prefix, workspace_id)
                    conn.execute(
                        text(
                            f'''
                            ALTER TABLE "{schema_name}"."{table_name}"
                            ADD COLUMN "{INTERNAL_TENANT_COLUMN}" TEXT
                            '''
                        )
                    )
                    conn.execute(
                        text(
                            f'''
                            UPDATE "{schema_name}"."{table_name}"
                            SET "{INTERNAL_TENANT_COLUMN}" = :legacy_value
                            '''
                        ),
                        {"legacy_value": f"legacy-{table_name}"},
                    )
                    conn.execute(
                        text(
                            f"""
                            COMMENT ON COLUMN "{schema_name}"."{table_name}"."{INTERNAL_TENANT_COLUMN}"
                            IS '{legacy_comments[table_name]}'
                            """
                        )
                    )
        finally:
            engine.dispose()

        _run_alembic_upgrade(test_db["db_url"])

        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                table_column_names = conn.execute(
                    text(
                        """
                        SELECT tc.name
                        FROM table_column AS tc
                        JOIN tables AS t ON tc.table_id = t.id
                        WHERE t.workspace_id = :workspace_id
                          AND t.name = :table_name
                        """
                    ),
                    {"workspace_id": workspace_id, "table_name": TABLES_TABLE},
                ).fetchall()
                assert [row[0] for row in table_column_names] == [LEGACY_TENANT_COLUMN]

                case_field_schema = conn.execute(
                    text(
                        """
                        SELECT schema
                        FROM case_field
                        WHERE workspace_id = :workspace_id
                        """
                    ),
                    {"workspace_id": workspace_id},
                ).scalar_one()
                assert LEGACY_TENANT_COLUMN in case_field_schema
                assert INTERNAL_TENANT_COLUMN not in case_field_schema
        finally:
            engine.dispose()

        _run_alembic_downgrade(test_db["db_url"])

        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                for prefix, table_name in (
                    (TABLES_PREFIX, TABLES_TABLE),
                    (CUSTOM_FIELDS_PREFIX, CUSTOM_FIELDS_TABLE),
                ):
                    schema_name = _workspace_schema(prefix, workspace_id)
                    columns = conn.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = :schema_name
                              AND table_name = :table_name
                            """
                        ),
                        {"schema_name": schema_name, "table_name": table_name},
                    ).fetchall()
                    column_names = {row[0] for row in columns}
                    assert INTERNAL_TENANT_COLUMN in column_names
                    assert LEGACY_TENANT_COLUMN not in column_names

                    restored_value = conn.execute(
                        text(
                            f'''
                            SELECT "{INTERNAL_TENANT_COLUMN}"
                            FROM "{schema_name}"."{table_name}"
                            '''
                        )
                    ).scalar_one()
                    assert restored_value == f"legacy-{table_name}"
                    assert (
                        _get_column_comment(
                            conn,
                            schema_name=schema_name,
                            table_name=table_name,
                            column_name=INTERNAL_TENANT_COLUMN,
                        )
                        == legacy_comments[table_name]
                    )

                table_column_names = conn.execute(
                    text(
                        """
                        SELECT tc.name
                        FROM table_column AS tc
                        JOIN tables AS t ON tc.table_id = t.id
                        WHERE t.workspace_id = :workspace_id
                          AND t.name = :table_name
                        """
                    ),
                    {"workspace_id": workspace_id, "table_name": TABLES_TABLE},
                ).fetchall()
                assert [row[0] for row in table_column_names] == [
                    INTERNAL_TENANT_COLUMN
                ]

                case_field_schema = conn.execute(
                    text(
                        """
                        SELECT schema
                        FROM case_field
                        WHERE workspace_id = :workspace_id
                        """
                    ),
                    {"workspace_id": workspace_id},
                ).scalar_one()
                assert INTERNAL_TENANT_COLUMN in case_field_schema
                assert LEGACY_TENANT_COLUMN not in case_field_schema
        finally:
            engine.dispose()

    def test_downgrade_restores_legacy_internal_namespace_columns_and_metadata(
        self, test_db
    ) -> None:
        legacy_comments = {
            TABLES_TABLE: "alerts legacy namespace comment",
            CUSTOM_FIELDS_TABLE: "case fields legacy namespace comment",
        }
        workspace_id = test_db["workspace_ids"][0]

        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                _seed_legacy_internal_metadata(
                    conn,
                    workspace_id=workspace_id,
                    column_name=LEGACY_INTERNAL_COLUMN,
                )
                for prefix, table_name in (
                    (TABLES_PREFIX, TABLES_TABLE),
                    (CUSTOM_FIELDS_PREFIX, CUSTOM_FIELDS_TABLE),
                ):
                    schema_name = _workspace_schema(prefix, workspace_id)
                    conn.execute(
                        text(
                            f'''
                            ALTER TABLE "{schema_name}"."{table_name}"
                            ADD COLUMN "{LEGACY_INTERNAL_COLUMN}" TEXT
                            '''
                        )
                    )
                    conn.execute(
                        text(
                            f'''
                            UPDATE "{schema_name}"."{table_name}"
                            SET "{LEGACY_INTERNAL_COLUMN}" = :legacy_value
                            '''
                        ),
                        {"legacy_value": f"legacy-{table_name}"},
                    )
                    conn.execute(
                        text(
                            f"""
                            COMMENT ON COLUMN "{schema_name}"."{table_name}"."{LEGACY_INTERNAL_COLUMN}"
                            IS '{legacy_comments[table_name]}'
                            """
                        )
                    )
        finally:
            engine.dispose()

        _run_alembic_upgrade(test_db["db_url"])

        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                table_column_names = conn.execute(
                    text(
                        """
                        SELECT tc.name
                        FROM table_column AS tc
                        JOIN tables AS t ON tc.table_id = t.id
                        WHERE t.workspace_id = :workspace_id
                          AND t.name = :table_name
                        """
                    ),
                    {"workspace_id": workspace_id, "table_name": TABLES_TABLE},
                ).fetchall()
                assert [row[0] for row in table_column_names] == [
                    MIGRATED_INTERNAL_COLUMN
                ]

                case_field_schema = conn.execute(
                    text(
                        """
                        SELECT schema
                        FROM case_field
                        WHERE workspace_id = :workspace_id
                        """
                    ),
                    {"workspace_id": workspace_id},
                ).scalar_one()
                assert MIGRATED_INTERNAL_COLUMN in case_field_schema
                assert LEGACY_INTERNAL_COLUMN not in case_field_schema
        finally:
            engine.dispose()

        _run_alembic_downgrade(test_db["db_url"])

        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                for prefix, table_name in (
                    (TABLES_PREFIX, TABLES_TABLE),
                    (CUSTOM_FIELDS_PREFIX, CUSTOM_FIELDS_TABLE),
                ):
                    schema_name = _workspace_schema(prefix, workspace_id)
                    columns = conn.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = :schema_name
                              AND table_name = :table_name
                            """
                        ),
                        {"schema_name": schema_name, "table_name": table_name},
                    ).fetchall()
                    column_names = {row[0] for row in columns}
                    assert LEGACY_INTERNAL_COLUMN in column_names
                    assert MIGRATED_INTERNAL_COLUMN not in column_names

                    restored_value = conn.execute(
                        text(
                            f'''
                            SELECT "{LEGACY_INTERNAL_COLUMN}"
                            FROM "{schema_name}"."{table_name}"
                            '''
                        )
                    ).scalar_one()
                    assert restored_value == f"legacy-{table_name}"
                    assert (
                        _get_column_comment(
                            conn,
                            schema_name=schema_name,
                            table_name=table_name,
                            column_name=LEGACY_INTERNAL_COLUMN,
                        )
                        == legacy_comments[table_name]
                    )

                table_column_names = conn.execute(
                    text(
                        """
                        SELECT tc.name
                        FROM table_column AS tc
                        JOIN tables AS t ON tc.table_id = t.id
                        WHERE t.workspace_id = :workspace_id
                          AND t.name = :table_name
                        """
                    ),
                    {"workspace_id": workspace_id, "table_name": TABLES_TABLE},
                ).fetchall()
                assert [row[0] for row in table_column_names] == [
                    LEGACY_INTERNAL_COLUMN
                ]

                case_field_schema = conn.execute(
                    text(
                        """
                        SELECT schema
                        FROM case_field
                        WHERE workspace_id = :workspace_id
                        """
                    ),
                    {"workspace_id": workspace_id},
                ).scalar_one()
                assert LEGACY_INTERNAL_COLUMN in case_field_schema
                assert MIGRATED_INTERNAL_COLUMN not in case_field_schema
        finally:
            engine.dispose()

    def test_upgrade_backfills_tenant_column_and_sets_default(self, test_db) -> None:
        _run_alembic_upgrade(test_db["db_url"])

        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                for workspace_id in test_db["workspace_ids"]:
                    for prefix, table_name in (
                        (TABLES_PREFIX, TABLES_TABLE),
                        (CUSTOM_FIELDS_PREFIX, CUSTOM_FIELDS_TABLE),
                    ):
                        schema_name = _workspace_schema(prefix, workspace_id)
                        column = conn.execute(
                            text(
                                """
                                SELECT is_nullable, column_default
                                FROM information_schema.columns
                                WHERE table_schema = :schema_name
                                  AND table_name = :table_name
                                  AND column_name = :column_name
                                """
                            ),
                            {
                                "schema_name": schema_name,
                                "table_name": table_name,
                                "column_name": INTERNAL_TENANT_COLUMN,
                            },
                        ).one()

                        assert column.is_nullable == "NO"
                        assert str(workspace_id) in (column.column_default or "")
                        assert "::uuid" in (column.column_default or "")

                        values = conn.execute(
                            text(
                                f'''
                                SELECT "{INTERNAL_TENANT_COLUMN}"
                                FROM "{schema_name}"."{table_name}"
                                '''
                            )
                        ).fetchall()
                        assert {row[0] for row in values} == {workspace_id}

                workspace_id = test_db["workspace_ids"][0]
                schema_name = _workspace_schema(TABLES_PREFIX, workspace_id)
                new_row_id = uuid.uuid4()
                conn.execute(
                    text(
                        f'''
                        INSERT INTO "{schema_name}"."{TABLES_TABLE}" (id, name)
                        VALUES (:row_id, :name)
                        '''
                    ),
                    {"row_id": new_row_id, "name": "default-backed"},
                )
                new_row = conn.execute(
                    text(
                        f'''
                        SELECT "{INTERNAL_TENANT_COLUMN}"
                        FROM "{schema_name}"."{TABLES_TABLE}"
                        WHERE id = :row_id
                        '''
                    ),
                    {"row_id": new_row_id},
                ).one()
                assert new_row[0] == workspace_id
        finally:
            engine.dispose()

    def test_upgrade_enables_dynamic_rls_policy(self, test_db) -> None:
        _run_alembic_upgrade(test_db["db_url"])

        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                workspace_a, workspace_b = test_db["workspace_ids"]
                for prefix, table_name in (
                    (TABLES_PREFIX, TABLES_TABLE),
                    (CUSTOM_FIELDS_PREFIX, CUSTOM_FIELDS_TABLE),
                ):
                    schema_name = _workspace_schema(prefix, workspace_a)
                    policy = conn.execute(
                        text(
                            """
                            SELECT policyname
                            FROM pg_policies
                            WHERE schemaname = :schema_name
                              AND tablename = :table_name
                            """
                        ),
                        {"schema_name": schema_name, "table_name": table_name},
                    ).fetchall()
                    assert [row[0] for row in policy] == [DYNAMIC_WORKSPACE_RLS_POLICY]

                    relrowsecurity = conn.execute(
                        text(
                            """
                            SELECT c.relrowsecurity
                            FROM pg_class c
                            JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = :schema_name
                              AND c.relname = :table_name
                            """
                        ),
                        {"schema_name": schema_name, "table_name": table_name},
                    ).scalar_one()
                    assert relrowsecurity is True

                    visible_rows = _dynamic_table_access(
                        conn,
                        role_name=test_db["reader_role"],
                        schema_name=schema_name,
                        table_name=table_name,
                        workspace_id=workspace_a,
                    )
                    hidden_rows = _dynamic_table_access(
                        conn,
                        role_name=test_db["reader_role"],
                        schema_name=schema_name,
                        table_name=table_name,
                        workspace_id=workspace_b,
                    )
                    bypass_rows = _dynamic_table_access(
                        conn,
                        role_name=test_db["reader_role"],
                        schema_name=schema_name,
                        table_name=table_name,
                        workspace_id=workspace_b,
                        bypass=True,
                    )

                    expected_row_id = test_db["row_ids"][table_name][workspace_a]
                    assert visible_rows == [expected_row_id]
                    assert hidden_rows == []
                    assert bypass_rows == [expected_row_id]
        finally:
            engine.dispose()

    def test_downgrade_removes_dynamic_rls_policy_and_tenant_column(
        self, test_db
    ) -> None:
        _run_alembic_upgrade(test_db["db_url"])
        _run_alembic_downgrade(test_db["db_url"])

        engine = create_engine(test_db["db_url"])
        try:
            with engine.begin() as conn:
                for workspace_id in test_db["workspace_ids"]:
                    for prefix, table_name in (
                        (TABLES_PREFIX, TABLES_TABLE),
                        (CUSTOM_FIELDS_PREFIX, CUSTOM_FIELDS_TABLE),
                    ):
                        schema_name = _workspace_schema(prefix, workspace_id)
                        column_exists = conn.execute(
                            text(
                                """
                                SELECT EXISTS(
                                    SELECT 1
                                    FROM information_schema.columns
                                    WHERE table_schema = :schema_name
                                      AND table_name = :table_name
                                      AND column_name = :column_name
                                )
                                """
                            ),
                            {
                                "schema_name": schema_name,
                                "table_name": table_name,
                                "column_name": INTERNAL_TENANT_COLUMN,
                            },
                        ).scalar_one()
                        assert column_exists is False

                        policies = conn.execute(
                            text(
                                """
                                SELECT policyname
                                FROM pg_policies
                                WHERE schemaname = :schema_name
                                  AND tablename = :table_name
                                """
                            ),
                            {"schema_name": schema_name, "table_name": table_name},
                        ).fetchall()
                        assert policies == []
        finally:
            engine.dispose()
