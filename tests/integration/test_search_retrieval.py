"""Live pgvector/Redis ranking and real HTTP-provider query coverage."""

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from tests.integration import test_embedding_configuration as providers
from tests.integration import test_search_indexing as indexing
from tests.integration import test_table_search_lifecycle as lifecycle
from tracecat import config
from tracecat.db.models import (
    AgentCatalog,
    AgentModelAccess,
    Organization,
    OrganizationSecret,
    SearchCollection,
    Workspace,
)
from tracecat.exceptions import ScopeDeniedError, TracecatNotFoundError
from tracecat.redis.client import RedisClient
from tracecat.search.cursors import SearchWindow, WindowStore
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.service import resolve_embedding_configuration
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    ModelSpec,
    PinnedConfiguration,
)
from tracecat.search.retrieval import TableRetrievalService
from tracecat.search.schemas import SearchRequest
from tracecat.search.types import (
    ChunkerSettings,
    ChunkManifest,
    EmbeddingResult,
    EnumerationCursor,
    SearchError,
    SearchErrorCode,
)
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableCreate, TableRowInsert
from tracecat.tables.service import TablesService

pytestmark = pytest.mark.anyio
tables = lifecycle.tables
table = lifecycle.table
workflow_bucket = lifecycle.workflow_bucket
scoped_database = indexing.scoped_database
close_redis_after_test = indexing.close_redis_after_test
provider_server = providers.provider_server


@dataclass
class RetrievalCase:
    tables: TablesService
    collection: SearchCollection
    config: PinnedConfiguration
    service: TableRetrievalService
    server: providers.ProviderServer
    name: str

    async def row(
        self,
        text: str = "synthetic passage",
        vectors: list[tuple[float, ...]] | None = None,
    ):
        table = await self.tables.get_table_by_name(self.name)
        row = await self.tables.insert_row(table, TableRowInsert(data={"body": text}))
        store = self.tables.search
        doc = await store.touch_document(
            self.collection.id, UUID(str(row["id"])), backfill=True
        )
        claim = await store.claim(self.collection.id, doc.id)
        assert claim is not None
        vectors = vectors or [(1.0,) * self.config.spec.dimensions]
        chunks = tuple(
            ChunkManifest(
                ordinal=i,
                column_id=self.collection.selected_column_ids[0],
                column_name="body",
                start=0,
                end=len(text),
                input_hash=f"{i:064x}",
            )
            for i in range(len(vectors))
        )
        await store.checkpoint(
            claim,
            before=EnumerationCursor(),
            after=EnumerationCursor(
                next_ordinal=len(chunks), character_offset=len(text)
            ),
            chunks=chunks,
            complete=True,
        )
        await store.write_embeddings(
            claim,
            tuple(
                EmbeddingResult(i, f"{i:064x}", claim.config_version, vector)
                for i, vector in enumerate(vectors)
            ),
        )
        await store.publish(claim)
        await self.tables.session.commit()
        return UUID(str(row["id"]))

    async def search(self, **kwargs):
        return await self.service.search(
            self.name, SearchRequest(query="synthetic query", **kwargs)
        )


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )


@pytest.fixture
async def retrieval(tables, table, provider_server):
    server, port = provider_server
    catalog = AgentCatalog(
        organization_id=tables.organization_id,
        model_provider="openai",
        model_name="synthetic-chat",
    )
    tables.session.add(catalog)
    await tables.session.flush()
    tables.session.add_all(
        [
            AgentModelAccess(
                organization_id=tables.organization_id, catalog_id=catalog.id
            ),
            OrganizationSecret(
                organization_id=tables.organization_id,
                name="agent-openai-credentials",
                environment="default",
                encrypted_keys=providers.encrypted({"OPENAI_API_KEY": "synthetic"}),
            ),
        ]
    )
    await tables.session.commit()
    selected = await lifecycle.enable(tables, table)
    scope = tables.search.scope
    pinned = await resolve_embedding_configuration(scope)
    assert pinned is not None
    collection = await tables.search.configure_collection(
        source_id=table.id,
        column_ids=tuple(selected.selected_column_ids),
        chunker=ChunkerSettings(tokenizer=pinned.spec.tokenizer),
        expected_generation=selected.generation,
    )
    await tables.search.checkpoint_backfill(
        collection.id,
        generation=collection.generation,
        before=None,
        after=None,
        complete=True,
    )
    await tables.session.commit()
    async with httpx.AsyncClient(
        transport=providers.LocalProviderTransport(port)
    ) as http:
        yield RetrievalCase(
            tables,
            collection,
            pinned,
            TableRetrievalService(tables.role, EmbeddingClient(http)),
            server,
            table.name,
        )


async def test_row_max_before_limit_unicode_excerpt_and_ties(retrieval):
    case = retrieval
    dim = case.config.spec.dimensions
    best = (1.0,) * dim
    long_row = await case.row("😀α " * 700, [best] * 8)
    other = await case.row(vectors=[best])
    lower = await case.row(vectors=[(1.0,) + (0.0,) * (dim - 1)])
    page = await case.search(limit=2)
    assert [item.row_id for item in page.items] == sorted([long_row, other])
    match = next(item.match for item in page.items if item.row_id == long_row)
    assert match.text == ("😀α " * 700)[:1000]
    assert (match.start, match.end, match.shortened) == (0, 1000, True)
    next_page = await case.search(limit=2, cursor=page.next_cursor)
    assert [item.row_id for item in next_page.items] == [lower]
    assert len(case.server.calls) == 1
    replay = await case.search(limit=2, cursor=page.next_cursor)
    assert replay == next_page


async def test_partial_readiness_and_no_partial_documents(retrieval):
    case = retrieval
    ready = await case.row()
    table = await case.tables.get_table_by_name(case.name)
    await case.tables.insert_row(
        table, TableRowInsert(data={"body": "not yet indexed"})
    )
    with pytest.raises(SearchError) as error:
        await case.search()
    assert error.value.code == SearchErrorCode.INDEX_NOT_READY
    assert not case.server.calls
    page = await case.search(allow_partial=True)
    assert page.index.partial and page.index.pending == 1
    assert [item.row_id for item in page.items] == [ready]


async def test_cursor_context_tampering_expiry_and_eviction(retrieval):
    case = retrieval
    await case.row()
    await case.row()
    page = await case.search(limit=1)
    cursor = page.next_cursor
    assert cursor is not None
    for request in [
        SearchRequest(query="other query", limit=1, cursor=cursor),
        SearchRequest(query="synthetic query", limit=2, cursor=cursor),
        SearchRequest(
            query="synthetic query",
            limit=1,
            cursor=cursor[:-1] + ("a" if cursor[-1] != "a" else "b"),
        ),
    ]:
        with pytest.raises(SearchError) as error:
            await case.service.search(case.name, request)
        assert error.value.code == SearchErrorCode.INVALID_CURSOR
    store = WindowStore(case.service.scope)
    client = await RedisClient()._get_client()
    await client.pexpire(store.prefix + cursor.split(".")[0], 1)
    await asyncio.sleep(0.01)
    with pytest.raises(SearchError):
        await case.search(limit=1, cursor=cursor)
    page = await case.search(limit=1)
    assert page.next_cursor
    raw = await client.get(store.prefix + page.next_cursor.split(".")[0])
    assert raw is not None
    window = SearchWindow.model_validate_json(raw)
    for _ in range(32):
        await store.save(window)
    with pytest.raises(SearchError):
        await case.search(limit=1, cursor=page.next_cursor)


async def test_deleted_and_edited_rows_are_skipped_without_refill(retrieval):
    case = retrieval
    ids = sorted([await case.row() for _ in range(4)])
    page = await case.search(limit=1, allow_partial=True)
    assert page.items[0].row_id == ids[0]
    table = await case.tables.get_table_by_name(case.name)
    await case.tables.delete_row(table, ids[1])
    await case.tables.update_row(table, ids[2], {"body": "changed"})
    last = await case.search(limit=1, allow_partial=True, cursor=page.next_cursor)
    assert [item.row_id for item in last.items] == [ids[3]]
    assert not last.has_more
    assert len(case.server.calls) == 1


async def test_missing_provider_even_partial_and_authorization_first(retrieval):
    case = retrieval
    await case.row()
    denied = TableRetrievalService(
        case.tables.role.model_copy(update={"scopes": frozenset()})
    )
    with pytest.raises(ScopeDeniedError):
        await denied.search(case.name, SearchRequest(query="synthetic"))
    with pytest.raises(TracecatNotFoundError):
        await case.service.search("absent_synthetic", SearchRequest(query="synthetic"))
    await case.tables.session.execute(
        sa.delete(OrganizationSecret).where(
            OrganizationSecret.organization_id == case.tables.organization_id
        )
    )
    await case.tables.session.commit()
    with pytest.raises(EmbeddingError) as error:
        await case.search(allow_partial=True)
    assert error.value.code == EmbeddingErrorCode.NOT_CONFIGURED
    assert not case.server.calls


async def test_query_budget_and_provider_failure_are_errors(retrieval):
    case = retrieval
    await case.row()
    with pytest.raises(EmbeddingError) as error:
        await case.service.search(case.name, SearchRequest(query="word " * 600))
    assert error.value.code == EmbeddingErrorCode.INPUT_INVALID
    assert not case.server.calls
    case.server.status = 429
    with pytest.raises(EmbeddingError) as error:
        await case.search()
    assert error.value.code == EmbeddingErrorCode.RATE_LIMITED


async def test_configuration_change_during_embedding_rejects_result(retrieval):
    case = retrieval
    await case.row()
    case.server.hold = True
    task = asyncio.create_task(case.search())
    assert await asyncio.to_thread(case.server.entered.wait, 5)
    await case.tables.session.execute(
        sa.delete(OrganizationSecret).where(
            OrganizationSecret.organization_id == case.tables.organization_id
        )
    )
    await case.tables.session.commit()
    case.server.release.set()
    with pytest.raises(EmbeddingError) as error:
        await task
    assert error.value.code == EmbeddingErrorCode.CONFIGURATION_CHANGED


async def test_top_100_window_is_explicitly_capped(retrieval):
    case = retrieval
    ids = sorted([await case.row() for _ in range(101)])
    page = await case.search(limit=100)
    assert [item.row_id for item in page.items] == ids[:100]
    assert page.capped and not page.has_more


async def test_cursor_rejects_other_actor_and_generation_change(retrieval):
    case = retrieval
    await case.row()
    await case.row()
    page = await case.search(limit=1)
    other = TableRetrievalService(
        case.tables.role.model_copy(update={"user_id": uuid4()})
    )
    with pytest.raises(SearchError) as error:
        await other.search(
            case.name,
            SearchRequest(query="synthetic query", limit=1, cursor=page.next_cursor),
        )
    assert error.value.code == SearchErrorCode.INVALID_CURSOR
    await case.tables.search.lock_scope()
    case.collection.generation += 1
    await case.tables.session.commit()
    with pytest.raises(SearchError):
        await case.search(limit=1, cursor=page.next_cursor)
    assert len(case.server.calls) == 1


async def test_edit_during_embedding_rechecks_strict_readiness(retrieval):
    case = retrieval
    row_id = await case.row()
    case.server.hold = True
    task = asyncio.create_task(case.search())
    assert await asyncio.to_thread(case.server.entered.wait, 5)
    table = await case.tables.get_table_by_name(case.name)
    await case.tables.update_row(table, row_id, {"body": "edited during query"})
    case.server.release.set()
    with pytest.raises(SearchError) as error:
        await task
    assert error.value.code == SearchErrorCode.INDEX_NOT_READY


async def test_other_workspace_mixed_dimensions_are_filtered_before_cosine(retrieval):
    case = retrieval
    own = await case.row()
    org_id, workspace_id = uuid4(), uuid4()
    session = case.tables.session
    session.add(Organization(id=org_id, name="Synthetic isolated", slug=str(org_id)))
    await session.flush()
    session.add(
        Workspace(id=workspace_id, organization_id=org_id, name="Synthetic isolated")
    )
    await session.commit()
    role = case.tables.role.model_copy(
        update={"organization_id": org_id, "workspace_id": workspace_id}
    )
    other_tables = TablesService(session, role)
    table = await other_tables.create_table(
        TableCreate(
            name="other_" + uuid4().hex,
            columns=[TableColumnCreate(name="body", type=SqlType.TEXT)],
        )
    )
    collection = await lifecycle.enable(other_tables, table, provider=True)
    other = RetrievalCase(
        other_tables,
        collection,
        PinnedConfiguration(
            1,
            ModelSpec(model="text-embedding-3-small", dimensions=3),
            uuid4(),
            "default",
        ),
        TableRetrievalService(role),
        case.server,
        table.name,
    )
    foreign = await other.row(vectors=[(1.0, 1.0, 1.0)])
    page = await case.search()
    assert [item.row_id for item in page.items] == [own]
    assert foreign != own
