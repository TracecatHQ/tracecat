"""Unique lookup indexes must coexist with auxiliary PostgreSQL indexes."""

import csv
from collections.abc import AsyncIterator, Iterator
from io import StringIO
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat.auth.types import Role
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.db.models import Organization, Workspace
from tracecat.pagination import CursorPaginationParams
from tracecat.tables.common import quote_identifier
from tracecat.tables.enums import SqlType
from tracecat.tables.importer import CSVImporter
from tracecat.tables.router import _build_table_read
from tracecat.tables.schemas import TableColumnCreate, TableCreate, TableRowInsert
from tracecat.tables.service import TablesService

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """These PostgreSQL regressions do not use object storage."""
    yield


@pytest.fixture(autouse=True)
def clean_redis_db() -> Iterator[None]:
    """Unique-index discovery and row writes do not use Redis."""
    yield


@pytest.fixture
async def tables_service() -> AsyncIterator[TablesService]:
    """Own transactions so a failed concurrent index build is observable."""
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    organization_id, workspace_id = uuid4(), uuid4()
    try:
        async with sessions() as session:
            session.add(
                Organization(
                    id=organization_id,
                    name="Synthetic index tests",
                    slug=str(organization_id),
                )
            )
            await session.flush()
            session.add(
                Workspace(
                    id=workspace_id,
                    organization_id=organization_id,
                    name="Synthetic index tests",
                )
            )
            await session.commit()
            role = Role(
                type="user",
                organization_id=organization_id,
                workspace_id=workspace_id,
                user_id=uuid4(),
                service_id="tracecat-api",
                scopes=ORG_ADMIN_SCOPES,
            )
            yield TablesService(session, role)
            await session.rollback()
    finally:
        await engine.dispose()


async def test_unique_lookup_coexists_with_search_indexes_and_lifecycle(
    tables_service: TablesService,
) -> None:
    service = tables_service
    table = await service.create_table(
        TableCreate(
            name="index_coexistence",
            columns=[
                TableColumnCreate(name="key", type=SqlType.TEXT),
                TableColumnCreate(name="body", type=SqlType.TEXT),
                TableColumnCreate(name="score", type=SqlType.INTEGER),
            ],
        )
    )
    conn = await service.session.connection()
    # Extension setup belongs only to this isolated test database.
    await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    full_name = service._full_table_name(table.name)
    auxiliary_indexes = (
        "CREATE INDEX search_full_text ON %s USING gin "
        "(to_tsvector('simple'::regconfig, body))",
        "CREATE INDEX search_trigram ON %s USING gin (body gin_trgm_ops)",
        "CREATE INDEX ordering_score ON %s (score DESC)",
        "CREATE INDEX ordering_key ON %s (key)",
    )
    for ddl in auxiliary_indexes:
        await conn.execute(sa.DDL(ddl, (full_name,)))

    assert await service.get_index(table) == []
    await service.create_unique_index(table, "key")
    assert await service.get_index(table) == ["key"]
    with pytest.raises(ValueError, match="multiple unique indexes"):
        await service.create_unique_index(table, "score")

    row = await service.insert_row(
        table, TableRowInsert(data={"key": "first", "body": "initial", "score": 1})
    )
    updated = await service.insert_row(
        table,
        TableRowInsert(
            data={"key": "first", "body": "single update", "score": 2}, upsert=True
        ),
    )
    assert updated["id"] == row["id"]
    assert updated["body"] == "single update"
    assert (
        await service.batch_insert_rows(
            table,
            [
                {"key": "first", "body": "batch update", "score": 3},
                {"key": "second", "body": "batch insert", "score": 4},
            ],
            upsert=True,
        )
        == 2
    )
    assert (await service.get_row(table, row["id"]))["body"] == "batch update"

    importer = CSVImporter(table.columns)
    reader = csv.DictReader(StringIO("key,body,score\nthird,csv import,5\n"))
    imported_rows = [
        importer.map_row(csv_row, {"key": "key", "body": "body", "score": "score"})
        for csv_row in reader
    ]
    await importer.process_chunk(imported_rows, service, table)
    assert importer.total_rows_inserted == 1
    metadata = await _build_table_read(service, table.id)
    assert {column.name for column in metadata.columns if column.is_index} == {"key"}
    rows = await service.list_rows(table, CursorPaginationParams())
    assert {item["key"] for item in rows.items} == {"first", "second", "third"}

    await service.drop_unique_index(table, "key")
    assert await service.get_index(table) == []
    conn = await service.session.connection()
    remaining_indexes = await conn.run_sync(
        lambda sync_conn: {
            index["name"]
            for index in sa.inspect(sync_conn).get_indexes(
                table.name, schema=service._get_schema_name()
            )
        }
    )
    assert remaining_indexes == {
        "search_full_text",
        "search_trigram",
        "ordering_score",
        "ordering_key",
    }
    await service.create_unique_index(table, "key")
    assert await service.get_index(table) == ["key"]


async def test_unsupported_unique_indexes_are_not_lookup_targets(
    tables_service: TablesService,
) -> None:
    service = tables_service
    table = await service.create_table(
        TableCreate(
            name="unsupported_indexes",
            columns=[
                TableColumnCreate(name="key", type=SqlType.TEXT),
                TableColumnCreate(name="body", type=SqlType.TEXT),
                TableColumnCreate(name="score", type=SqlType.INTEGER),
            ],
        )
    )
    conn = await service.session.connection()
    full_name = service._full_table_name(table.name)
    unsupported_indexes = (
        "CREATE UNIQUE INDEX partial_key ON %s (key) WHERE body IS NOT NULL",
        "CREATE UNIQUE INDEX expression_key ON %s (lower(key))",
        "CREATE UNIQUE INDEX composite_key ON %s (key, body)",
        "CREATE UNIQUE INDEX covering_key ON %s (key) INCLUDE (body)",
        "ALTER TABLE %s ADD CONSTRAINT deferred_score UNIQUE (score) "
        "DEFERRABLE INITIALLY DEFERRED",
    )
    for ddl in unsupported_indexes:
        await conn.execute(sa.DDL(ddl, (full_name,)))
    assert await service.get_index(table) == []
    assert not any(
        column.is_index
        for column in (await _build_table_read(service, table.id)).columns
    )
    with pytest.raises(ValueError, match="No unique index exists"):
        await service.drop_unique_index(table, "key")
    with pytest.raises(ValueError, match="at least one unique index"):
        await service.insert_row(
            table,
            TableRowInsert(data={"key": "first", "body": "duplicate"}, upsert=True),
        )
    with pytest.raises(ValueError, match="at least one unique index"):
        await service.batch_insert_rows(
            table, [{"key": "first", "body": "duplicate"}], upsert=True
        )

    await service.batch_insert_rows(
        table,
        [
            {"key": "first", "body": "duplicate", "score": 1},
            {"key": "second", "body": "duplicate", "score": 2},
        ],
    )
    await service.session.commit()
    concurrent_engine = create_async_engine(
        TEST_DB_CONFIG.test_url, isolation_level="AUTOCOMMIT"
    )
    try:
        async with concurrent_engine.connect() as concurrent_conn:
            with pytest.raises(IntegrityError):
                await concurrent_conn.execute(
                    sa.DDL(
                        "CREATE UNIQUE INDEX CONCURRENTLY invalid_body ON %s (body)",
                        (full_name,),
                    )
                )
            validity = (
                await concurrent_conn.execute(
                    sa.text(
                        "SELECT indisvalid, indisready FROM pg_catalog.pg_index "
                        "WHERE indexrelid = to_regclass(:index_name)"
                    ),
                    {
                        "index_name": (
                            f"{quote_identifier(service._get_schema_name())}.invalid_body"
                        )
                    },
                )
            ).one()
            assert validity == (False, False)
    finally:
        await concurrent_engine.dispose()

    assert await service.get_index(table) == []
    await service.create_unique_index(table, "key")
    assert await service.get_index(table) == ["key"]
    await service.insert_row(
        table,
        TableRowInsert(
            data={"key": "first", "body": "single update", "score": 1}, upsert=True
        ),
    )
    assert (
        await service.batch_insert_rows(
            table, [{"key": "third", "body": "batch insert", "score": 3}], upsert=True
        )
        == 1
    )

    conn = await service.session.connection()
    await conn.execute(
        sa.DDL(
            "ALTER TABLE %s ADD CONSTRAINT deferred_key UNIQUE (key) "
            "DEFERRABLE INITIALLY DEFERRED",
            (full_name,),
        )
    )
    assert await service.get_index(table) == ["key"]
    with pytest.raises(ValueError, match="deferrable unique constraint"):
        await service.insert_row(
            table, TableRowInsert(data={"key": "fourth", "body": "single"}, upsert=True)
        )
    with pytest.raises(ValueError, match="deferrable unique constraint"):
        await service.batch_insert_rows(
            table, [{"key": "fourth", "body": "batch"}], upsert=True
        )
    rows = await service.list_rows(table, CursorPaginationParams())
    assert {item["key"] for item in rows.items} == {"first", "second", "third"}
