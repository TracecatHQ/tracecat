"""Startup-owned Temporal dispatcher with bounded, text-free history."""

import asyncio
from datetime import timedelta
from uuid import UUID

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    import httpx
    import sqlalchemy as sa
    from opentelemetry import metrics

    from tracecat.db.engine import get_async_session_bypass_rls_context_manager
    from tracecat.db.models import SearchCollection
    from tracecat.search.capacity import search_capacity
    from tracecat.search.cleanup import cleanup_orphans
    from tracecat.search.embeddings.client import EmbeddingClient
    from tracecat.search.embeddings.service import (
        embed_current,
        resolve_embedding_configuration,
    )
    from tracecat.search.embeddings.types import EmbeddingError
    from tracecat.search.indexing import index_collection
    from tracecat.search.indexing_types import (
        CollectionWork,
        DispatchPage,
        IndexingProgress,
    )
    from tracecat.search.types import SearchError


@activity.defn
async def discover_search_collections(cursor: UUID | None) -> DispatchPage:
    """Privileged maintenance discovery returns trusted IDs only."""
    try:
        async with get_async_session_bypass_rls_context_manager() as session:
            await cleanup_orphans(session, limit=100)
            stmt = sa.select(SearchCollection).order_by(SearchCollection.id).limit(33)
            if cursor is not None:
                stmt = stmt.where(SearchCollection.id > cursor)
            collections = (await session.scalars(stmt)).all()
            result = DispatchPage(
                collections=[
                    CollectionWork(
                        organization_id=c.organization_id,
                        workspace_id=c.workspace_id,
                        collection_id=c.id,
                    )
                    for c in collections[:32]
                ],
                next_cursor=collections[31].id if len(collections) > 32 else None,
            )
            await session.commit()
            return result
    except Exception:
        pass
    # Raised outside except: don't serialize SQL parameters or error context.
    raise ApplicationError("Search discovery unavailable", type="SEARCH_UNAVAILABLE")


@activity.defn
async def index_search_collection(work: CollectionWork) -> IndexingProgress:
    """One bounded activity; distributed admission also bounds DB concurrency."""
    safe_error: str | None = None
    try:
        async with (
            asyncio.timeout(90),
            search_capacity(work.scope, background=True) as acquired,
        ):
            if not acquired:
                return IndexingProgress(outcome="capacity")
            configuration = await resolve_embedding_configuration(work.scope)
            async with httpx.AsyncClient() as http:
                client = EmbeddingClient(http)
                result = await index_collection(
                    work, configuration, lambda request: embed_current(request, client)
                )
            meter = metrics.get_meter("tracecat.search")
            tags = {"outcome": result.outcome}
            meter.create_counter("search.indexing.batches").add(1, tags)
            for name, count in (
                ("prepared", result.prepared),
                ("embedded", result.embedded),
                ("cleaned", result.cleaned),
            ):
                meter.create_counter(f"search.indexing.{name}").add(count)
            meter.create_histogram("search.indexing.pending_rows").record(
                result.pending
            )
            meter.create_histogram("search.indexing.failed_rows").record(result.failed)
            meter.create_histogram(
                "search.indexing.queue_wait_seconds", unit="s"
            ).record(result.queue_wait_seconds)
            if result.total_tokens is not None:
                meter.create_counter("search.indexing.provider_tokens").add(
                    result.total_tokens
                )
            return result
    except (EmbeddingError, SearchError) as exc:
        safe_error = exc.code.value
    except Exception:
        safe_error = "SEARCH_UNAVAILABLE"
    raise ApplicationError(safe_error, type="SEARCH_UNAVAILABLE")


@workflow.defn
class SearchIndexDispatcher:
    """Give collections bounded turns; later schedule runs rediscover all work."""

    @workflow.run
    async def run(self, cursor: UUID | None = None) -> None:
        for _ in range(32):
            page = await workflow.execute_activity(
                discover_search_collections,
                cursor,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            remaining = list(page.collections)
            while remaining:
                wave: list[CollectionWork] = []
                deferred: list[CollectionWork] = []
                workspaces: set[UUID] = set()
                for item in remaining:
                    if len(wave) == 6 or item.workspace_id in workspaces:
                        deferred.append(item)
                    else:
                        wave.append(item)
                        workspaces.add(item.workspace_id)
                remaining = deferred
                # One turn per workspace per wave avoids permit starvation.
                results = await asyncio.gather(
                    *[
                        workflow.execute_activity(
                            index_search_collection,
                            item,
                            start_to_close_timeout=timedelta(seconds=100),
                            retry_policy=RetryPolicy(maximum_attempts=1),
                        )
                        for item in wave
                    ],
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, ActivityError):
                        workflow.logger.warning(
                            "Search collection deferred after activity failure"
                        )
            cursor = page.next_cursor
            if cursor is None:
                return
        workflow.continue_as_new(cursor)
