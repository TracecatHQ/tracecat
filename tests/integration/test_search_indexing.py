"""Real PostgreSQL/Redis and controlled HTTP embedding lifecycle coverage."""

import json
import threading
from collections.abc import Iterator
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tests.database import TEST_DB_CONFIG
from tests.integration import test_table_search_lifecycle as lifecycle
from tests.integration.test_table_search_lifecycle import documents, enable
from tracecat import config
from tracecat.db.engine import reset_async_engine
from tracecat.db.models import SearchChunk, SearchDocument, Table
from tracecat.redis.client import RedisClient
from tracecat.search.capacity import search_capacity
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    ModelSpec,
    PinnedConfiguration,
    ResolvedCredential,
)
from tracecat.search.indexing import index_collection
from tracecat.search.indexing_schedule import SCHEDULE_ID, ensure_search_schedule
from tracecat.search.indexing_types import CollectionWork, IndexingProgress
from tracecat.search.indexing_workflow import (
    SearchIndexDispatcher,
    discover_search_collections,
)
from tracecat.search.types import (
    EmbeddingRequest,
    EnumerationCursor,
    SearchScope,
    SearchState,
)
from tracecat.tables.schemas import TableRowInsert
from tracecat.tables.service import TablesService

pytestmark = pytest.mark.anyio
tables = lifecycle.tables
table = lifecycle.table
workflow_bucket = lifecycle.workflow_bucket


@pytest.fixture(autouse=True)
def scoped_database(monkeypatch: pytest.MonkeyPatch, env_sandbox):
    monkeypatch.setattr(config, "TRACECAT__DB_URI", TEST_DB_CONFIG.test_url_sync)
    reset_async_engine()
    yield
    reset_async_engine()


@pytest.fixture(autouse=True)
async def close_redis_after_test():
    yield
    await RedisClient().close()


@pytest.fixture
def embedding_server() -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload = json.dumps(
                {
                    "model": body["model"],
                    "data": [
                        {"index": i, "embedding": [1.0, 0.5, 0.25]}
                        for i, _ in enumerate(body["input"])
                    ],
                    "usage": {"prompt_tokens": 7, "total_tokens": 7},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/embeddings"
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
async def indexing(tables: TablesService, table: Table, embedding_server: str):
    collection = await enable(tables, table, provider=True)
    spec = ModelSpec(
        model="text-embedding-3-small",
        dimensions=3,
        endpoint=embedding_server,
        input_token_limit=40,
        batch_size_limit=4,
        batch_token_limit=100,
    )
    pinned = PinnedConfiguration(1, spec, uuid4(), "default")
    work = CollectionWork(
        organization_id=tables.organization_id,
        workspace_id=tables.workspace_id,
        collection_id=collection.id,
    )
    async with httpx.AsyncClient() as http:
        client = EmbeddingClient(http)

        async def embed(request: EmbeddingRequest):
            return await client.embed(
                pinned, ResolvedCredential({"OPENAI_API_KEY": "synthetic"}), request
            )

        yield work, pinned, embed


async def test_long_document_resumes_and_never_publishes_partial(
    tables: TablesService, table: Table, indexing
):
    work, pinned, embed = indexing
    row = await tables.insert_row(
        table, TableRowInsert(data={"body": "synthetic paragraph α😀. " * 300})
    )
    saw_partial = False
    ordinals: set[int] = set()

    async def tracked(request):
        new = {item.ordinal for item in request.items}
        assert not ordinals.intersection(new)
        ordinals.update(new)
        return await embed(request)

    for _ in range(100):
        progress = await index_collection(work, pinned, tracked)
        await tables.session.rollback()
        doc = await tables.session.scalar(
            sa.select(SearchDocument).where(SearchDocument.source_row_id == row["id"])
        )
        assert doc is not None
        if progress.outcome == "published":
            assert doc.indexed_revision == doc.desired_revision
            assert doc.enumeration_complete
            assert doc.expected_chunks == len(ordinals)
            break
        assert progress.outcome == "progress"
        assert doc.indexed_revision is None
        checkpoint = EnumerationCursor.model_validate_json(
            json.dumps(doc.enumeration_cursor)
        )
        assert checkpoint.chunker is not None
        assert checkpoint.chunker.identity.document_id == doc.id
        saw_partial = True
    else:
        pytest.fail("bounded retries did not finish")
    assert saw_partial and len(ordinals) > 32


async def test_edit_during_embedding_rejects_old_vectors(
    tables: TablesService, table: Table, indexing
):
    work, pinned, embed = indexing
    row = await tables.insert_row(table, TableRowInsert(data={"body": "old text"}))

    async def edit(request):
        result = await embed(request)
        await tables.update_row(table, row["id"], {"body": "replacement"})
        return result

    assert (await index_collection(work, pinned, edit)).outcome == "STALE_CLAIM"
    doc = (
        await documents(
            tables.session, await tables.search.collection(work.collection_id)
        )
    )[0]
    assert doc.indexed_revision is None and doc.desired_revision == 3
    await tables.session.commit()
    assert (await index_collection(work, pinned, embed)).outcome == "published"


async def test_failure_retries_retain_successful_chunks(
    tables: TablesService, table: Table, indexing
):
    work, pinned, embed = indexing
    await tables.insert_row(
        table, TableRowInsert(data={"body": "synthetic long text. " * 100})
    )
    assert (await index_collection(work, pinned, embed)).embedded > 0

    async def rate_limit(request):
        raise EmbeddingError(EmbeddingErrorCode.RATE_LIMITED, retry_after=1)

    assert (await index_collection(work, pinned, rate_limit)).outcome == "RATE_LIMITED"
    await tables.session.rollback()
    doc = await tables.session.scalar(
        sa.select(SearchDocument).where(
            SearchDocument.collection_id == work.collection_id
        )
    )
    assert doc is not None and doc.state == "failed" and doc.next_attempt_at is not None
    count = await tables.session.scalar(
        sa.select(sa.func.count())
        .select_from(SearchChunk)
        .where(SearchChunk.document_id == doc.id, SearchChunk.state == "embedded")
    )
    assert count and doc.indexed_revision is None
    # Permanent failure stops automatic retries; explicit retry remains possible.
    doc.next_attempt_at = datetime.now(UTC)
    await tables.session.commit()

    async def bad_credentials(request):
        raise EmbeddingError(EmbeddingErrorCode.CREDENTIAL_INVALID)

    await index_collection(work, pinned, bad_credentials)
    await tables.session.rollback()
    await tables.session.refresh(doc)
    assert doc.state == "failed" and doc.next_attempt_at is None
    assert doc.error_code == "CREDENTIAL_INVALID"


async def test_no_provider_and_pause_preserve_work(
    tables: TablesService, table: Table, indexing
):
    work, pinned, embed = indexing
    await tables.insert_row(table, TableRowInsert(data={"body": "pending"}))

    async def forbidden(request):
        pytest.fail("provider must not be called")

    assert (await index_collection(work, None, forbidden)).outcome == "unavailable"
    await tables.search.set_state(SearchState.PAUSED)
    await tables.session.commit()
    assert (await index_collection(work, pinned, forbidden)).outcome == "paused"
    await tables.search.set_state(SearchState.ACTIVE)
    await tables.session.commit()
    assert (await index_collection(work, pinned, embed)).outcome == "published"


async def test_distributed_capacity_reserves_foreground(tables: TablesService):
    async with AsyncExitStack() as stack:
        assert await stack.enter_async_context(
            search_capacity(tables.search.scope, background=True)
        )
        assert not await stack.enter_async_context(
            search_capacity(tables.search.scope, background=True)
        )
        assert await stack.enter_async_context(search_capacity(tables.search.scope))
        assert not await stack.enter_async_context(search_capacity(tables.search.scope))
    async with search_capacity(tables.search.scope, background=True) as acquired:
        assert acquired


async def test_temporal_dispatch_discovers_committed_row_and_schedule(
    tables: TablesService, table: Table, indexing
):
    work, pinned, embed = indexing
    await tables.insert_row(
        table, TableRowInsert(data={"body": "committed before dispatch"})
    )

    @activity.defn(name="index_search_collection")
    async def controlled_activity(item: CollectionWork) -> IndexingProgress:
        if item.collection_id != work.collection_id:
            return IndexingProgress(outcome="idle")
        return await index_collection(item, pinned, embed)

    async with await WorkflowEnvironment.start_local(
        data_converter=pydantic_data_converter
    ) as environment:
        queue = "synthetic-search-" + uuid4().hex
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[SearchIndexDispatcher],
            activities=[discover_search_collections, controlled_activity],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            await ensure_search_schedule(environment.client, queue)
            handle = environment.client.get_schedule_handle(SCHEDULE_ID)
            await handle.pause()
            await ensure_search_schedule(environment.client, queue)
            assert (await handle.describe()).schedule.state.paused
            await environment.client.execute_workflow(
                SearchIndexDispatcher.run, None, id=uuid4().hex, task_queue=queue
            )
            await handle.delete()
    await tables.session.rollback()
    doc = await tables.session.scalar(
        sa.select(SearchDocument).where(
            SearchDocument.collection_id == work.collection_id
        )
    )
    assert (
        doc is not None
        and doc.state == "ready"
        and doc.indexed_revision == doc.desired_revision
    )


async def test_large_document_yields_to_short_row(
    tables: TablesService, table: Table, indexing
):
    work, pinned, embed = indexing
    await tables.insert_row(
        table, TableRowInsert(data={"body": "long synthetic paragraph. " * 400})
    )
    short = await tables.insert_row(table, TableRowInsert(data={"body": "short"}))
    outcomes = [(await index_collection(work, pinned, embed)).outcome for _ in range(2)]
    assert set(outcomes) == {"progress", "published"}
    await tables.session.rollback()
    doc = await tables.session.scalar(
        sa.select(SearchDocument).where(SearchDocument.source_row_id == short["id"])
    )
    assert doc is not None and doc.state == "ready"


async def test_global_capacity_spans_workspaces(tables: TablesService):
    scopes = [SearchScope(tables.organization_id, uuid4()) for _ in range(9)]
    async with AsyncExitStack() as stack:
        for scope in scopes[:6]:
            assert await stack.enter_async_context(
                search_capacity(scope, background=True)
            )
        assert not await stack.enter_async_context(
            search_capacity(scopes[6], background=True)
        )
        for scope in scopes[6:8]:
            assert await stack.enter_async_context(search_capacity(scope))
        assert not await stack.enter_async_context(search_capacity(scopes[8]))


async def test_crash_after_preparation_resumes_saved_checkpoint(
    tables: TablesService, table: Table, indexing
):
    work, pinned, embed = indexing
    await tables.insert_row(
        table, TableRowInsert(data={"body": "resumable synthetic text. " * 100})
    )

    async def crash(request):
        raise RuntimeError("synthetic process failure")

    with pytest.raises(RuntimeError):
        await index_collection(work, pinned, crash)
    await tables.session.rollback()
    doc = await tables.session.scalar(
        sa.select(SearchDocument).where(
            SearchDocument.collection_id == work.collection_id
        )
    )
    assert (
        doc is not None
        and doc.enumeration_cursor is not None
        and doc.indexed_revision is None
    )
    checkpoint = dict(doc.enumeration_cursor)
    old_fence = doc.fence
    doc.lease_until = datetime.now(UTC)
    await tables.session.commit()
    result = await index_collection(work, pinned, embed)
    assert result.embedded > 0 and result.prepared == 0
    await tables.session.refresh(doc)
    assert doc.enumeration_cursor == checkpoint and doc.fence > old_fence
