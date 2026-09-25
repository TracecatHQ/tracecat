"""Cross-service acceptance with live pgvector/Redis/Temporal and HTTP embeddings.

The controlled vectors establish ranking/state correctness, not model relevance.
The API transport and authenticated role are injected; browser/full executor QA
is a separate acceptance gate documented in README.md.
"""

import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import get_args
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from tracecat_registry.context import RegistryContext, clear_context, set_context
from tracecat_registry.core.table import search
from tracecat_registry.sdk.client import TracecatClient
from tracecat_registry.sdk.exceptions import (
    TracecatAPIError,
    TracecatConflictError,
)

from tests.integration import test_embedding_configuration as providers
from tests.integration import test_search_indexing as indexing
from tests.integration import test_search_retrieval as retrieval
from tests.integration import test_table_search_lifecycle as lifecycle
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.db.models import SearchChunk, SearchDocument
from tracecat.search import router
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.service import (
    embed_current,
    resolve_embedding_configuration,
)
from tracecat.search.indexing import index_collection
from tracecat.search.indexing_types import (
    CollectionWork,
    IndexingOutcome,
    IndexingProgress,
)
from tracecat.search.indexing_workflow import (
    SearchIndexDispatcher,
    discover_search_collections,
)
from tracecat.search.retrieval import TableRetrievalService
from tracecat.search.types import SearchState
from tracecat.tables.schemas import TableRowInsert
from tracecat.tables.search.schemas import TableSearchDisplayState, TableSearchSelection

pytestmark = pytest.mark.anyio
tables = lifecycle.tables
table = lifecycle.table
workflow_bucket = lifecycle.workflow_bucket
scoped_database = indexing.scoped_database
close_redis_after_test = indexing.close_redis_after_test
encryption_key = retrieval.encryption_key
retrieval_case = retrieval.retrieval


@pytest.fixture
def provider_server():
    state = providers.ProviderServer()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.calls.append(self.path)
            vectors = []
            for text in payload["input"]:
                # Tail passage and paraphrase share a controlled axis. Filler has
                # an orthogonal vector, so a tail match cannot pass by accident.
                hit = "stolen credentials" in text or "account takeover" in text
                vectors.append(([1.0, 0.0] if hit else [0.0, 1.0]) + [0.0] * 1534)
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "model": payload["model"],
                        "data": [
                            {"index": i, "embedding": v} for i, v in enumerate(vectors)
                        ],
                        "usage": {"prompt_tokens": 1, "total_tokens": 1},
                    }
                ).encode()
            )

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, server.server_port
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
async def journey(retrieval_case, provider_server, monkeypatch):
    case = retrieval_case
    tables = case.tables
    table = await tables.get_table_by_name(case.name)
    selected = await tables.search.select_column(
        table.id,
        TableSearchSelection(
            column_id=next(c.id for c in table.columns if c.name == "key"),
            enabled=True,
            expected_generation=case.collection.generation,
        ),
    )
    assert selected is not None
    await tables.session.commit()
    _, port = provider_server
    async with httpx.AsyncClient(
        transport=providers.LocalProviderTransport(port)
    ) as http:
        client = EmbeddingClient(http)
        monkeypatch.setattr(
            router,
            "TableRetrievalService",
            partial(TableRetrievalService, client=client),
        )
        app = FastAPI()
        app.include_router(router.router, prefix="/internal/tables")
        app.dependency_overrides[get_args(ExecutorWorkspaceRole)[1].dependency] = (
            lambda: tables.role
        )

        class GatewayTransport(httpx.AsyncHTTPTransport):
            async def handle_async_request(
                self, request: httpx.Request
            ) -> httpx.Response:
                return await httpx.ASGITransport(app=app).handle_async_request(request)

        class LocalClient(TracecatClient):
            def _request_url_and_transport(self, path):
                return f"http://synthetic/internal{path}", GatewayTransport()

        set_context(
            RegistryContext(
                workspace_id=str(tables.workspace_id),
                workflow_id=str(uuid4()),
                run_id=str(uuid4()),
                _client=LocalClient(action_gateway_socket="/unused/synthetic.sock"),
            )
        )
        work = CollectionWork(
            organization_id=tables.organization_id,
            workspace_id=tables.workspace_id,
            collection_id=selected.id,
        )

        @activity.defn(name="index_search_collection")
        async def controlled_activity(item: CollectionWork) -> IndexingProgress:
            if item.collection_id != work.collection_id:
                return IndexingProgress(outcome=IndexingOutcome.IDLE)
            pinned = await resolve_embedding_configuration(item.scope)
            return await index_collection(
                item, pinned, lambda request: embed_current(request, client)
            )

        try:
            async with await WorkflowEnvironment.start_local(
                data_converter=pydantic_data_converter
            ) as temporal:
                queue = "acceptance-" + uuid4().hex
                async with Worker(
                    temporal.client,
                    task_queue=queue,
                    workflows=[SearchIndexDispatcher],
                    activities=[discover_search_collections, controlled_activity],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ):

                    async def ready(max_turns=100, observe=None):
                        for turn in range(max_turns):
                            handle = await temporal.client.start_workflow(
                                SearchIndexDispatcher.run,
                                None,
                                id=uuid4().hex,
                                task_queue=queue,
                            )
                            await handle.result()
                            if turn % 10 == 0:
                                history = await handle.fetch_history()
                                assert len(history.events) <= 30
                                payload = b"".join(
                                    event.SerializeToString()
                                    for event in history.events
                                )
                                for forbidden in (
                                    b"stolen credentials",
                                    b"Ordinary inventory",
                                    b"OPENAI_API_KEY",
                                    b'"embedding"',
                                    b'"vector"',
                                    b"account takeover",
                                ):
                                    assert forbidden not in payload
                            if observe is not None and turn % 10 == 0:
                                await observe()
                            await tables.session.rollback()
                            status = await tables.search.configuration(table.id)
                            await tables.session.commit()
                            if status.status == TableSearchDisplayState.READY:
                                return status
                            assert (
                                status.status != TableSearchDisplayState.NEEDS_ATTENTION
                            ), status
                        pytest.fail(
                            f"index did not reach Ready after {max_turns} bounded dispatches"
                        )

                    yield case, table, ready
        finally:
            clear_context()


async def test_index_to_action_tail_excerpt_pagination_and_source_changes(journey):
    case, table, ready = journey
    tables = case.tables
    # More than one provider batch, with the only relevant passage near the end.
    body = (
        "Ordinary inventory maintenance. " * 10000
        + "stolen credentials enabled a sign-in."
    )
    long = await tables.insert_row(
        table, TableRowInsert(data={"key": "long", "body": body, "count": 0})
    )
    short = await tables.insert_row(
        table,
        TableRowInsert(data={"key": "stolen credentials", "body": "short", "count": 0}),
    )
    await tables.insert_row(
        table,
        TableRowInsert(
            data={"key": "unrelated", "body": "Office supplies", "count": 0}
        ),
    )
    with pytest.raises(TracecatConflictError, match="INDEX_NOT_READY"):
        await search(table=case.name, query="account takeover", limit=1)
    status = await ready()
    assert status.index is not None and status.index.ready == 3
    first = await search(table=case.name, query="account takeover", limit=1)
    assert first["items"][0]["score"] == pytest.approx(1)
    calls = len(case.server.calls)
    second = await search(
        table=case.name, query="account takeover", limit=1, cursor=first["next_cursor"]
    )
    assert len(case.server.calls) == calls  # Continuation never embeds again.
    matches = first["items"] + second["items"]
    assert {item["row_id"] for item in matches} == {str(long["id"]), str(short["id"])}
    tail = next(item for item in matches if item["row_id"] == str(long["id"]))["match"]
    assert tail["start"] > len(body) // 2
    assert tail["text"] == body[tail["start"] : tail["end"]]
    assert len(tail["text"]) <= 1000
    # A bounded excerpt may end before the matching phrase. Its offsets must
    # still identify the winning chunk, and the full source is read separately.
    assert tail["column_name"] == "body"
    before = await lifecycle.documents(tables.session, case.collection)
    revision = next(d.desired_revision for d in before if d.source_row_id == long["id"])
    await tables.update_row(table, long["id"], {"count": 1})
    after = await lifecycle.documents(tables.session, case.collection)
    assert (
        next(d.desired_revision for d in after if d.source_row_id == long["id"])
        == revision
    )
    await tables.session.commit()
    assert (
        await tables.search.configuration(table.id)
    ).status == TableSearchDisplayState.READY
    await tables.session.commit()

    await tables.update_row(
        table, long["id"], {"body": "Short replacement with no matching words."}
    )
    with pytest.raises(TracecatConflictError, match="INDEX_NOT_READY"):
        await search(table=case.name, query="account takeover")
    partial_page = await search(
        table=case.name, query="account takeover", allow_partial=True
    )
    assert str(long["id"]) not in {item["row_id"] for item in partial_page["items"]}
    await ready()
    updated = await search(table=case.name, query="account takeover")
    assert updated["items"][0]["row_id"] == str(short["id"])
    assert next(item for item in updated["items"] if item["row_id"] == str(long["id"]))[
        "score"
    ] == pytest.approx(0)
    await tables.delete_row(table, short["id"])
    await ready()
    deleted = await search(table=case.name, query="account takeover")
    assert str(short["id"]) not in {item["row_id"] for item in deleted["items"]}
    stale = await tables.session.scalar(
        sa.select(sa.func.count())
        .select_from(SearchChunk)
        .join(SearchDocument, SearchChunk.document_id == SearchDocument.id)
        .where(SearchDocument.source_row_id == UUID(str(short["id"])))
    )
    assert stale == 0


async def test_outage_pause_and_old_writer_rebuild_preserve_source(journey):
    """Recover real indexed rows after outage/pause and an untracked old write."""

    case, table, ready = journey
    tables = case.tables
    row = await tables.insert_row(
        table,
        TableRowInsert(
            data={"key": "synthetic", "body": "stolen credentials", "count": 0}
        ),
    )
    await ready()
    await tables.search.set_state(SearchState.PAUSED)
    await tables.session.commit()
    await tables.update_row(table, row["id"], {"body": "replacement while paused"})
    assert (await tables.get_row(table, row["id"]))[
        "body"
    ] == "replacement while paused"
    await tables.session.commit()
    # Paused queries cannot return the previously published revision.
    with pytest.raises(TracecatConflictError, match="INDEX_NOT_READY"):
        await search(table=case.name, query="account takeover", allow_partial=True)
    await tables.search.set_state(SearchState.ACTIVE)
    await tables.session.commit()
    await ready()
    assert (await search(table=case.name, query="account takeover"))["items"][0][
        "score"
    ] == pytest.approx(0)

    case.server.status = 429
    await tables.update_row(
        table, row["id"], {"body": "stolen credentials during outage"}
    )
    with pytest.raises(TracecatAPIError) as error:
        await search(table=case.name, query="account takeover", allow_partial=True)
    assert error.value.status_code == 429
    # Source writes remain usable independently of query embedding availability.
    assert (await tables.get_row(table, row["id"]))[
        "body"
    ] == "stolen credentials during outage"
    await tables.session.commit()
    case.server.status = 200
    await ready()

    await tables.search.set_state(SearchState.REINDEX_REQUIRED)
    await tables.search.set_state(SearchState.PAUSED)
    await tables.session.commit()
    # An old writer is represented by SQL that deliberately bypasses the current
    # service's search bookkeeping. Use only this fixture's generated identifiers.
    physical = sa.table(
        tables._sanitize_identifier(table.name),
        sa.column("id"),
        sa.column("body"),
        schema=tables._get_schema_name(),
    )
    await tables.session.execute(
        sa.update(physical)
        .where(physical.c.id == row["id"])
        .values(body="old writer replacement")
    )
    await tables.session.commit()
    with pytest.raises(TracecatConflictError, match="INDEX_NOT_READY"):
        await search(table=case.name, query="account takeover", allow_partial=True)
    # A query reconciled a fresh configuration while preserving the pause.
    await tables.search.set_state(SearchState.ACTIVE)
    await tables.session.commit()
    await ready()
    rebuilt = await search(table=case.name, query="account takeover")
    assert rebuilt["items"][0]["score"] == pytest.approx(0)
    assert rebuilt["items"][0]["match"]["text"] == "old writer replacement"
