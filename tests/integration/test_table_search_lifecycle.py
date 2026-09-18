"""Live PostgreSQL coverage for transactional table semantic indexing hooks."""

import asyncio
from collections.abc import Iterator
from typing import get_args
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.db.engine import get_async_session
from tracecat.db.models import (
    Organization,
    SearchChunk,
    SearchCollection,
    SearchDocument,
    Table,
    Workspace,
)
from tracecat.exceptions import ScopeDeniedError, TracecatNotFoundError
from tracecat.search.chunking_types import ChunkingIdentity
from tracecat.search.embeddings.schemas import EmbeddingConfigurationRead
from tracecat.search.query import eligible_chunks
from tracecat.search.types import (
    ChunkerSettings,
    ChunkManifest,
    EmbeddingResult,
    EnumerationCursor,
    SearchError,
    SearchErrorCode,
    SearchScope,
    SearchState,
)
from tracecat.tables.enums import SqlType
from tracecat.tables.importer import CSVImporter
from tracecat.tables.schemas import (
    TableColumnCreate,
    TableColumnUpdate,
    TableCreate,
    TableRowInsert,
    TableUpdate,
)
from tracecat.tables.search import TableSearchService
from tracecat.tables.search_router import router as search_router
from tracecat.tables.search_schemas import TableSearchDisplayState, TableSearchSelection
from tracecat.tables.search_source import TableSearchSource
from tracecat.tables.service import BaseTablesService, TableEditorService, TablesService
from tracecat.workspace_sync.adapters import TABLE_RESOURCE_ADAPTER
from tracecat.workspace_sync.importer import WorkspaceResourceImportService
from tracecat.workspace_sync.schemas import (
    TableColumnSpec,
    TableResourceSpec,
    WorkspaceSpec,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    yield


@pytest.fixture
async def tables():
    # Own real transactions so commits and concurrent connections are observable.
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    org_id, workspace_id = uuid4(), uuid4()
    async with sessions() as session:
        session.add(Organization(id=org_id, name="Synthetic search", slug=str(org_id)))
        await session.flush()
        session.add(
            Workspace(id=workspace_id, organization_id=org_id, name="Synthetic search")
        )
        await session.commit()
        role = Role(
            type="user",
            organization_id=org_id,
            workspace_id=workspace_id,
            user_id=uuid4(),
            service_id="tracecat-api",
            scopes=ORG_ADMIN_SCOPES,
        )
        yield TablesService(session, role)
        await session.rollback()
    await engine.dispose()


@pytest.fixture
async def table(tables: TablesService) -> Table:
    return await tables.create_table(
        TableCreate(
            name="search_" + uuid4().hex,
            columns=[
                TableColumnCreate(name="key", type=SqlType.TEXT),
                TableColumnCreate(name="body", type=SqlType.TEXT),
                TableColumnCreate(name="count", type=SqlType.INTEGER),
            ],
        )
    )


async def enable(
    tables: TablesService, table: Table, *, provider: bool = False
) -> SearchCollection:
    store = tables.search
    if provider:
        await store.save_configuration(
            provider="synthetic",
            model="synthetic",
            endpoint=None,
            credential_id=uuid4(),
            credential_environment="default",
            dimensions=3,
            input_token_limit=1024,
        )
        await store.set_state(SearchState.ACTIVE)
    selected = await store.select_column(
        table.id,
        TableSearchSelection(
            column_id=next(c.id for c in table.columns if c.name == "body"),
            enabled=True,
            expected_generation=0,
        ),
    )
    assert selected is not None
    if provider:
        selected = await store.configure_collection(
            source_id=table.id,
            column_ids=tuple(selected.selected_column_ids),
            chunker=ChunkerSettings(tokenizer="synthetic"),
            expected_generation=selected.generation,
        )
    await tables.session.commit()
    return selected


async def documents(
    session: AsyncSession, collection: SearchCollection
) -> list[SearchDocument]:
    return list(
        (
            await session.scalars(
                sa.select(SearchDocument)
                .where(SearchDocument.collection_id == collection.id)
                .order_by(SearchDocument.source_row_id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def test_selection_without_provider_and_generation_conflict(
    tables: TablesService, table: Table
):
    collection = await enable(tables, table)
    assert collection.config_version is None
    before = collection.generation
    same = await tables.search.select_column(
        table.id,
        TableSearchSelection(
            column_id=collection.selected_column_ids[0],
            enabled=True,
            expected_generation=before,
        ),
    )
    assert same is not None and same.generation == before
    with pytest.raises(SearchError):
        await tables.search.select_column(
            table.id,
            TableSearchSelection(
                column_id=collection.selected_column_ids[0],
                enabled=False,
                expected_generation=0,
            ),
        )
    status = await tables.search.configuration(table.id)
    assert status.status == TableSearchDisplayState.UNAVAILABLE
    row = await tables.insert_row(table, TableRowInsert(data={"body": "synthetic"}))
    docs = await documents(tables.session, collection)
    assert [(d.source_row_id, d.desired_revision) for d in docs] == [(row["id"], 1)]
    with pytest.raises(SearchError):
        await tables.search.claim(collection.id, docs[0].id)


async def test_selected_edits_and_deletion(tables: TablesService, table: Table):
    collection = await enable(tables, table)
    row = await tables.insert_row(
        table, TableRowInsert(data={"body": "first", "count": 1})
    )
    await tables.update_row(table, row["id"], {"count": 2})
    assert (await documents(tables.session, collection))[0].desired_revision == 1
    await tables.update_row(table, row["id"], {"body": "second"})
    doc = (await documents(tables.session, collection))[0]
    assert doc.desired_revision == 2 and doc.indexed_revision is None
    await tables.delete_row(table, row["id"])
    doc = (await documents(tables.session, collection))[0]
    assert doc.state == "deleted" and doc.deleted_at is not None


async def test_outer_rollback_and_savepoint(tables: TablesService, table: Table):
    collection = await enable(tables, table)
    table_id, collection_id = table.id, collection.id
    await tables.insert_row(
        table, TableRowInsert(data={"body": "rolled back"}), commit=False
    )
    await tables.session.rollback()
    table = await tables.get_table(table_id)
    collection = await tables.search.collection(collection_id)
    assert not await documents(tables.session, collection)
    async with tables.session.begin_nested() as savepoint:
        await BaseTablesService(tables.session, tables.role).insert_row(
            table, TableRowInsert(data={"body": "savepoint"})
        )
        await savepoint.rollback()
    await tables.session.commit()
    assert not await documents(tables.session, collection)
    assert not await tables.get_rows(table, [])


async def test_bulk_upsert_effective_values_and_unique_index(
    tables: TablesService, table: Table
):
    collection = await enable(tables, table)
    await tables.create_unique_index(table, "key")
    await tables.batch_insert_rows(
        table, [{"key": "one", "body": "keep"}, {"key": "two", "body": "change"}]
    )
    await tables.batch_insert_rows(
        table,
        [
            {"key": "one", "body": None},
            {"key": "two", "body": "new"},
            {"key": "three", "count": 1},
        ],
        upsert=True,
    )
    rows = await tables.get_rows(
        table, [d.source_row_id for d in await documents(tables.session, collection)]
    )
    by_id = rows
    docs = await documents(tables.session, collection)
    revisions = {by_id[d.source_row_id]["key"]: d.desired_revision for d in docs}
    assert revisions == {"one": 1, "two": 2, "three": 1}
    assert next(r for r in rows.values() if r["key"] == "one")["body"] == "keep"
    await tables.insert_row(
        table, TableRowInsert(data={"key": "one", "body": "single"}, upsert=True)
    )
    await tables.batch_update_rows(table, [d.source_row_id for d in docs], {"count": 2})
    assert sorted(
        d.desired_revision for d in await documents(tables.session, collection)
    ) == [1, 2, 2]
    await tables.batch_delete_rows(table, [d.source_row_id for d in docs])
    assert all(
        d.state == "deleted" for d in await documents(tables.session, collection)
    )


async def test_column_changes_and_table_rename(tables: TablesService, table: Table):
    collection = await enable(tables, table)
    column = await tables.get_column(table.id, collection.selected_column_ids[0])
    await tables.update_column(column, TableColumnUpdate(name="renamed"))
    assert (await tables.search.collection(collection.id)).generation == 2
    await tables.update_table(table, TableUpdate(name="renamed_" + uuid4().hex))
    assert (await tables.search.collection(collection.id)).generation == 2
    await tables.update_column(
        column, TableColumnUpdate(type=SqlType.SELECT, options=["synthetic"])
    )
    collection = await tables.search.collection(collection.id)
    assert collection.generation == 3 and not collection.enabled


async def test_bounded_reader_rejects_edits_and_backfill_catches_low_id(
    tables: TablesService, table: Table
):
    row = await tables.insert_row(table, TableRowInsert(data={"body": "α😀\n" * 10000}))
    collection = await enable(tables, table, provider=True)
    source = TableSearchSource(tables.session, tables.search.scope)
    page = await source.scan_rows(
        collection.id, generation=collection.generation, limit=1
    )
    assert page.rows[0].row_id == row["id"]
    doc = await source.touch_document(collection.id, row["id"], backfill=True)
    identity = ChunkingIdentity(
        organization_id=source.scope.organization_id,
        workspace_id=source.scope.workspace_id,
        collection_id=collection.id,
        document_id=doc.id,
        generation=collection.generation,
        config_version=1,
        revision=1,
    )
    piece = await source.read_slice(identity, collection.selected_column_ids[0], 1, 7)
    assert piece.text == ("α😀\n" * 10000)[1:8] and not piece.end_of_column
    await tables.session.commit()
    await tables.update_row(table, row["id"], {"body": "short"})
    with pytest.raises(SearchError):
        await source.read_slice(identity, collection.selected_column_ids[0], 0, 7)
    # The insertion hook handles IDs earlier than an already advanced scan cursor.
    low = UUID(int=1)
    await tables.session.execute(
        sa.text(
            f'ALTER TABLE "{tables._get_schema_name()}"."{table.name}" ALTER COLUMN id SET DEFAULT \'{low}\'::uuid'
        )
    )
    await tables.insert_row(table, TableRowInsert(data={"body": "late"}))
    assert low in [d.source_row_id for d in await documents(tables.session, collection)]


async def test_editor_managed_writes(tables: TablesService, table: Table):
    collection = await enable(tables, table)
    editor = TableEditorService(
        tables.session,
        tables.role,
        table_name=table.name,
        schema_name=tables._get_schema_name(),
    )
    row = await editor.insert_row(TableRowInsert(data={"body": "editor"}))
    await editor.update_row(row["id"], {"body": "edited"})
    await editor.update_column("body", TableColumnUpdate(name="text"))
    assert (await tables.search.collection(collection.id)).generation == 2
    await editor.delete_row(row["id"])
    assert (await documents(tables.session, collection))[0].state == "deleted"


async def test_enablement_waits_for_unconfigured_writer(
    tables: TablesService, table: Table
):
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    table_id = table.id
    column_id = next(c.id for c in table.columns if c.name == "body")
    await tables.session.commit()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def writer():
        async with sessions.begin() as session:
            service = BaseTablesService(session, tables.role)
            live = await service.get_table(table_id)
            await service.insert_row(live, TableRowInsert(data={"body": "racing"}))
            entered.set()
            await release.wait()

    async def select():
        async with sessions.begin() as session:
            store = TableSearchService(session, tables.search.scope)
            result = await store.select_column(
                table_id,
                TableSearchSelection(
                    column_id=column_id, enabled=True, expected_generation=0
                ),
            )
            assert result is not None
            return result.id

    try:
        task = asyncio.create_task(writer())
        await asyncio.wait_for(entered.wait(), 5)
        selection = asyncio.create_task(select())
        await asyncio.sleep(0.05)
        assert not selection.done()
        release.set()
        await asyncio.wait_for(task, 5)
        collection_id = await asyncio.wait_for(selection, 5)
        collection = await tables.search.collection(collection_id)
        assert not collection.backfill_complete
        assert (
            len(
                await tables.get_rows(
                    table,
                    [
                        r[0]
                        for r in (
                            await tables.session.execute(
                                sa.text(
                                    f'SELECT id FROM "{tables._get_schema_name()}"."{table.name}"'
                                )
                            )
                        ).all()
                    ],
                )
            )
            == 1
        )
    finally:
        release.set()
        await engine.dispose()


async def test_progress_retry_and_stale_generation(tables: TablesService, table: Table):
    collection = await enable(tables, table, provider=True)
    await tables.batch_insert_rows(table, [{"body": "one"}, {"body": "two"}])
    docs = await documents(tables.session, collection)
    claim = await tables.search.claim(collection.id, docs[0].id)
    assert claim is not None
    await tables.search.fail(claim, SearchErrorCode.PROVIDER_UNAVAILABLE)
    page = await tables.search.progress(
        table.id, generation=collection.generation, limit=1
    )
    assert page.has_more and page.next_cursor is not None
    assert page.items[0].sampled_chunks == 0 and page.items[0].expected_chunks is None
    page2 = await tables.search.progress(
        table.id, generation=collection.generation, cursor=page.next_cursor, limit=1
    )
    assert (
        not page2.has_more and page2.items[0].document_id != page.items[0].document_id
    )
    await tables.search.retry(table.id, collection.generation, [docs[0].id])
    assert (await documents(tables.session, collection))[0].state == "pending"
    with pytest.raises(SearchError):
        await tables.search.retry(table.id, collection.generation + 1, [docs[0].id])


async def test_csv_paths_and_selection_deletion(tables: TablesService, table: Table):
    collection = await enable(tables, table)
    importer = CSVImporter(list(table.columns), chunk_size=2)
    await importer.process_chunk(
        [{"body": "csv one"}, {"body": "csv two"}], tables, table
    )
    await tables._insert_import_chunk(table, [{"body": "csv three"}], chunk_size=2)
    assert len(await documents(tables.session, collection)) == 3
    key = next(c for c in table.columns if c.name == "key")
    await tables.search.select_column(
        table.id,
        TableSearchSelection(
            column_id=key.id, enabled=True, expected_generation=collection.generation
        ),
    )
    await tables.session.commit()
    body = await tables.get_column(
        table.id, next(c.id for c in table.columns if c.name == "body")
    )
    await tables.delete_column(body)
    collection = await tables.search.collection(collection.id)
    assert collection.enabled and collection.selected_column_ids == [key.id]
    await tables.delete_table(table)
    collection = await tables.search.collection(collection.id)
    assert not collection.enabled and collection.deleted_at is not None


async def test_source_isolation_and_model_version(tables: TablesService, table: Table):
    collection = await enable(tables, table, provider=True)
    await tables.insert_row(table, TableRowInsert(data={"body": "private synthetic"}))
    doc = (await documents(tables.session, collection))[0]
    source = TableSearchSource(tables.session, tables.search.scope)
    identity = ChunkingIdentity(
        organization_id=source.scope.organization_id,
        workspace_id=source.scope.workspace_id,
        collection_id=collection.id,
        document_id=doc.id,
        generation=collection.generation,
        config_version=1,
        revision=1,
    )
    with pytest.raises(SearchError):
        await source.read_slice(
            identity.model_copy(update={"workspace_id": uuid4()}),
            collection.selected_column_ids[0],
            0,
            10,
        )
    await tables.search.save_configuration(
        provider="synthetic",
        model="new",
        endpoint=None,
        credential_id=uuid4(),
        credential_environment="default",
        dimensions=3,
        input_token_limit=1024,
    )
    with pytest.raises(SearchError):
        await source.read_slice(identity, collection.selected_column_ids[0], 0, 10)
    status = await tables.search.configuration(table.id)
    assert status.status != TableSearchDisplayState.READY
    await tables.search.set_state(SearchState.PAUSED)
    await tables.update_row(table, doc.source_row_id, {"body": "while paused"})
    assert (await documents(tables.session, collection))[0].desired_revision == 2


async def test_column_selection_rejects_nontext_and_foreign_table(
    tables: TablesService, table: Table
):
    with pytest.raises(ValueError):
        await tables.search.select_column(
            table.id,
            TableSearchSelection(
                column_id=next(c.id for c in table.columns if c.name == "count"),
                enabled=True,
                expected_generation=0,
            ),
        )
    # A forged source ID cannot expose another workspace's metadata.
    workspace = Workspace(
        id=uuid4(), organization_id=tables.organization_id, name="Other synthetic"
    )
    tables.session.add(workspace)
    await tables.session.flush()
    other = TableSearchService(
        tables.session, SearchScope(tables.organization_id, workspace.id)
    )
    with pytest.raises(TracecatNotFoundError):
        await other.configuration(table.id)


async def test_workspace_sync_preserves_selection_and_invalidates_type_change(
    tables: TablesService, table: Table
):
    collection = await enable(tables, table)
    importer = WorkspaceResourceImportService(tables.session, tables.role)
    spec = WorkspaceSpec(
        tables={
            "synthetic": TableResourceSpec(
                id="synthetic",
                name=table.name,
                columns=[
                    TableColumnSpec(name=c.name, type=c.type.lower())
                    for c in table.columns
                ],
            )
        }
    )
    await importer.import_non_workflow_resources(spec)
    assert (await tables.search.collection(collection.id)).generation == 1
    spec.tables["synthetic"].columns[1] = TableColumnSpec(
        name="body", type="select", options=["synthetic"]
    )
    await importer.import_non_workflow_resources(spec)
    await tables.session.commit()
    collection = await tables.search.collection(collection.id)
    assert collection.generation == 2 and not collection.enabled
    projection = await TABLE_RESOURCE_ADAPTER.project(importer)
    assert "selected_column_ids" not in str(projection)
    assert "config_version" not in str(projection)


async def test_progress_caps_chunk_reads_and_never_reports_partial_row_ready(
    tables: TablesService, table: Table
):
    collection = await enable(tables, table, provider=True)
    await tables.insert_row(table, TableRowInsert(data={"body": "long synthetic"}))
    doc = (await documents(tables.session, collection))[0]
    tables.session.add_all(
        [
            SearchChunk(
                organization_id=tables.organization_id,
                workspace_id=tables.workspace_id,
                collection_id=collection.id,
                document_id=doc.id,
                generation=collection.generation,
                revision=1,
                config_version=1,
                dimensions=3,
                ordinal=n,
                column_id=collection.selected_column_ids[0],
                column_name="body",
                start_offset=n,
                end_offset=n + 1,
                input_hash=f"{n:064x}",
                embedding=[1, 0, 0],
                state="embedded",
            )
            for n in range(1025)
        ]
    )
    await tables.session.flush()
    page = await tables.search.progress(table.id, generation=collection.generation)
    assert page.items[0].chunks_capped
    assert page.items[0].sampled_chunks == page.items[0].sampled_embedded == 1001
    assert page.items[0].expected_chunks is None
    assert (
        await tables.search.configuration(table.id)
    ).status != TableSearchDisplayState.READY


async def test_late_worker_cannot_publish_after_source_edit(
    tables: TablesService, table: Table
):
    collection = await enable(tables, table, provider=True)
    row = await tables.insert_row(table, TableRowInsert(data={"body": "old"}))
    doc = (await documents(tables.session, collection))[0]
    claim = await tables.search.claim(collection.id, doc.id)
    assert claim is not None
    await tables.search.checkpoint(
        claim,
        before=EnumerationCursor(),
        after=EnumerationCursor(next_ordinal=1),
        chunks=(
            ChunkManifest(
                ordinal=0,
                column_id=collection.selected_column_ids[0],
                column_name="body",
                start=0,
                end=3,
                input_hash="a" * 64,
            ),
        ),
        complete=True,
    )
    await tables.search.write_embeddings(
        claim, (EmbeddingResult(0, "a" * 64, 1, (1, 0, 0)),)
    )
    await tables.search.publish(claim)
    await tables.session.commit()
    assert (
        len((await tables.session.scalars(eligible_chunks(tables.search.scope))).all())
        == 1
    )
    await tables.update_row(table, row["id"], {"body": "new"})
    assert not (
        await tables.session.scalars(eligible_chunks(tables.search.scope))
    ).all()
    with pytest.raises(SearchError):
        await tables.search.publish(claim)


async def test_configuration_and_source_rollback_together(
    tables: TablesService, table: Table
):
    table_id, column_id = table.id, table.columns[1].id
    await tables.search.select_column(
        table_id,
        TableSearchSelection(column_id=column_id, enabled=True, expected_generation=0),
    )
    await tables.insert_row(
        table, TableRowInsert(data={"body": "rollback"}), commit=False
    )
    await tables.session.rollback()
    assert await tables.search.for_table(table_id) is None


async def test_http_selection_conflict_validation_and_permissions(
    tables: TablesService, table: Table
):
    app = FastAPI()
    app.include_router(search_router, prefix="/tables")
    role = tables.role

    async def authenticated_role():
        return role

    async def database_session():
        yield tables.session

    app.dependency_overrides[get_args(WorkspaceActorRouteRole)[1].dependency] = (
        authenticated_role
    )
    app.dependency_overrides[get_async_session] = database_session

    async def forbidden(request, exc):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    app.add_exception_handler(ScopeDeniedError, forbidden)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = f"/tables/{table.id}/search"
        request = {
            "column_id": str(table.columns[1].id),
            "enabled": True,
            "expected_generation": 0,
        }
        response = await client.patch(path + "/selection", json=request)
        assert response.status_code == 200, response.text
        assert response.json()["generation"] == 1
        conflict = await client.patch(path + "/selection", json=request)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "CONFIGURATION_CHANGED"
        invalid = await client.patch(
            path + "/selection",
            json={
                **request,
                "expected_generation": 1,
                "column_id": str(table.columns[2].id),
            },
        )
        assert invalid.status_code == 422
        await tables.insert_row(table, TableRowInsert(data={"body": "http"}))
        progress = await client.get(path + "/documents", params={"generation": 1})
        assert progress.status_code == 200, progress.text
        assert len(progress.json()["items"]) == 1
        role = role.model_copy(update={"scopes": frozenset({"table:read"})})
        denied = await client.patch(
            path + "/selection", json={**request, "expected_generation": 1}
        )
        assert denied.status_code == 403
        denied_retry = await client.post(
            path + "/retry",
            json={"expected_generation": 1, "document_ids": [str(uuid4())]},
        )
        assert denied_retry.status_code == 403
        role = role.model_copy(update={"scopes": frozenset()})
        denied_read = await client.get(path + "/documents", params={"generation": 1})
        assert denied_read.status_code == 403


async def test_json_unique_keys_preserve_upsert_tracking(
    tables: TablesService, table: Table
):
    await tables.create_column(
        table, TableColumnCreate(name="json_key", type=SqlType.JSONB)
    )
    table = await tables.get_table(table.id, populate_existing=True)
    collection = await enable(tables, table)
    await tables.create_unique_index(table, "json_key")
    await tables.batch_insert_rows(
        table, [{"json_key": {"synthetic": 1}, "body": "original"}]
    )
    await tables.batch_insert_rows(
        table, [{"json_key": {"synthetic": 1}, "body": None}], upsert=True
    )
    assert (await documents(tables.session, collection))[0].desired_revision == 1
    await tables.batch_insert_rows(
        table, [{"json_key": {"synthetic": 1}, "body": "batch"}], upsert=True
    )
    await tables.insert_row(
        table,
        TableRowInsert(
            data={"json_key": {"synthetic": 1}, "body": "single"}, upsert=True
        ),
    )
    assert (await documents(tables.session, collection))[0].desired_revision == 3


async def test_bookkeeping_database_failure_rolls_back_source(
    tables: TablesService, table: Table
):
    collection = await enable(tables, table)
    table_id, collection_id = table.id, collection.id
    # A database-enforced bookkeeping failure must fail the source transaction.
    await tables.session.execute(
        sa.text(f"""ALTER TABLE search_document ADD CONSTRAINT synthetic_reject_document
        CHECK (collection_id <> '{collection_id}'::uuid) NOT VALID""")
    )
    await tables.session.commit()
    try:
        with pytest.raises(IntegrityError):
            await tables.insert_row(
                table, TableRowInsert(data={"body": "must roll back"})
            )
        await tables.session.rollback()
        table = await tables.get_table(table_id)
        count = await tables.session.scalar(
            sa.text(
                f'SELECT count(*) FROM "{tables._get_schema_name()}"."{table.name}"'
            )
        )
        assert count == 0
    finally:
        await tables.session.rollback()
        await tables.session.execute(
            sa.text(
                "ALTER TABLE search_document DROP CONSTRAINT synthetic_reject_document"
            )
        )
        await tables.session.commit()


async def test_readiness_tracks_provider_availability_and_pause(
    tables: TablesService, table: Table
):
    collection = await enable(tables, table, provider=True)
    collection.backfill_complete = True
    await tables.session.flush()
    assert (
        await tables.search.configuration(table.id)
    ).status == TableSearchDisplayState.READY
    unavailable = EmbeddingConfigurationRead(
        available=False, version=1, state=SearchState.DISABLED
    )
    status = await tables.search.configuration(table.id, availability=unavailable)
    assert (
        status.status == TableSearchDisplayState.UNAVAILABLE
        and status.index is not None
        and status.index.partial
    )
    changing = EmbeddingConfigurationRead(
        available=True, version=2, state=SearchState.ACTIVE, reindex_required=True
    )
    assert (
        await tables.search.configuration(table.id, availability=changing)
    ).status == TableSearchDisplayState.UPDATING
    failed = await tables.search.configuration(table.id, provider_error=True)
    assert failed.status == TableSearchDisplayState.NEEDS_ATTENTION
    paused = changing.model_copy(update={"state": SearchState.PAUSED})
    assert (
        await tables.search.configuration(table.id, availability=paused)
    ).status == TableSearchDisplayState.UNAVAILABLE


@pytest.mark.parametrize("batch", [False, True])
async def test_deletion_while_disabled_stays_deleted_after_reenable(
    tables: TablesService, table: Table, batch: bool
):
    collection = await enable(tables, table, provider=True)
    column_id = collection.selected_column_ids[0]
    row = await tables.insert_row(table, TableRowInsert(data={"body": "synthetic"}))
    await tables.search.select_column(
        table.id,
        TableSearchSelection(
            column_id=column_id,
            enabled=False,
            expected_generation=collection.generation,
        ),
    )
    await tables.session.commit()
    if batch:
        await tables.batch_delete_rows(table, [row["id"]])
    else:
        await tables.delete_row(table, row["id"])
    document = (await documents(tables.session, collection))[0]
    assert document.deleted_at is not None
    assert document.state == "deleted"
    await tables.search.select_column(
        table.id,
        TableSearchSelection(
            column_id=column_id,
            enabled=True,
            expected_generation=collection.generation,
        ),
    )
    source = TableSearchSource(tables.session, tables.search.scope)
    page = await source.scan_rows(collection.id, generation=collection.generation)
    assert page.rows == ()
    await source.checkpoint_backfill(
        collection.id,
        generation=collection.generation,
        before=None,
        after=None,
        complete=True,
    )
    status = await tables.search.configuration(table.id)
    assert status.index is not None
    assert status.index.pending == 0
    assert status.status == TableSearchDisplayState.READY
    assert await source.claim(collection.id, document.id) is None


async def test_source_reader_resolves_legacy_metadata_column_name(
    tables: TablesService, table: Table
):
    row = await tables.insert_row(table, TableRowInsert(data={"body": "α😀 synthetic"}))
    collection = await enable(tables, table, provider=True)
    column = await tables.get_column(table.id, collection.selected_column_ids[0])
    # Supported legacy metadata can differ from the sanitized physical name.
    column.name = "bo-dy"
    await tables.session.flush()
    source = TableSearchSource(tables.session, tables.search.scope)
    document = await source.touch_document(collection.id, row["id"], backfill=True)
    assert collection.config_version is not None
    identity = ChunkingIdentity(
        organization_id=source.scope.organization_id,
        workspace_id=source.scope.workspace_id,
        collection_id=collection.id,
        document_id=document.id,
        generation=collection.generation,
        config_version=collection.config_version,
        revision=document.desired_revision,
    )
    piece = await source.read_slice(identity, column.id, 1, 3)
    assert piece.text == "😀 s"
    assert not piece.end_of_column
