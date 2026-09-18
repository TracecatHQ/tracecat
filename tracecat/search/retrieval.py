"""Authorized semantic table search with stable bounded result windows."""

import asyncio
import hashlib
from uuid import UUID

import httpx
import orjson

from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.models import SearchCollection
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.search.capacity import search_capacity
from tracecat.search.cursors import SearchWindow, WindowStore
from tracecat.search.embeddings.catalog import token_counter
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.service import (
    embed_current,
    resolve_embedding_configuration,
)
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    PinnedConfiguration,
)
from tracecat.search.ranking import rank_rows, read_results
from tracecat.search.schemas import SearchPage, SearchRequest
from tracecat.search.types import (
    EmbeddingInput,
    EmbeddingRequest,
    SearchError,
    SearchErrorCode,
    SearchScope,
    SearchState,
)
from tracecat.tables.search_source import TableSearchSource
from tracecat.tables.service import TablesService


def window_context(
    role: Role,
    request: SearchRequest,
    collection: SearchCollection,
    configuration: PinnedConfiguration,
) -> str:
    """Bind the snapshot to current authorization, options and semantic identity."""
    identity = role.model_dump(mode="json", exclude={"scopes"})
    identity["scopes"] = sorted(role.scopes) if role.scopes is not None else None
    data = {
        "role": identity,
        "request": request.model_dump(exclude={"cursor"}),
        "collection": str(collection.id),
        "generation": collection.generation,
        "config": configuration.version,
    }
    return hashlib.sha256(orjson.dumps(data, option=orjson.OPT_SORT_KEYS)).hexdigest()


class TableRetrievalService:
    """Coordinate short source transactions around an independent provider call."""

    def __init__(self, role: Role, client: EmbeddingClient | None = None):
        if role.organization_id is None or role.workspace_id is None:
            raise TracecatAuthorizationError("Workspace context required")
        self.client = client
        self.role = role
        self.scope = SearchScope(role.organization_id, role.workspace_id)

    async def _collection(
        self,
        store: TableSearchSource,
        table_id: UUID,
        configuration: PinnedConfiguration,
        request: SearchRequest,
    ):
        collection = await store.for_table(table_id)
        if (
            collection is None
            or not collection.enabled
            or collection.deleted_at is not None
        ):
            raise SearchError(SearchErrorCode.INDEX_NOT_READY)
        state = await store._state()
        if state.current_version != configuration.version:
            raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
        if state.state != SearchState.ACTIVE:
            raise SearchError(SearchErrorCode.INDEX_NOT_READY)
        status = await store.status(collection.id)
        if collection.config_version != configuration.version:
            raise SearchError(
                SearchErrorCode.INVALID_CURSOR
                if request.cursor
                else SearchErrorCode.INDEX_NOT_READY
            )
        if status.partial and not request.allow_partial:
            raise SearchError(SearchErrorCode.INDEX_NOT_READY)
        return collection, status

    @require_scope("table:read")
    async def search(self, table_name: str, request: SearchRequest) -> SearchPage:
        """Authorize and reconcile on every page; continuations never re-embed."""
        async with TablesService.with_session(role=self.role) as tables:
            table_id = (await tables.get_table_by_name(table_name)).id
        configuration = await resolve_embedding_configuration(self.scope)
        if configuration is None:
            raise EmbeddingError(EmbeddingErrorCode.NOT_CONFIGURED)
        spec = configuration.spec
        if not request.query.strip() or len(request.query) > spec.input_character_limit:
            raise EmbeddingError(EmbeddingErrorCode.INPUT_INVALID)
        count = await asyncio.to_thread(token_counter(spec).count_tokens, request.query)
        if count > min(512, spec.input_token_limit, spec.batch_token_limit):
            raise EmbeddingError(EmbeddingErrorCode.INPUT_INVALID)
        async with TableSearchSource.with_session(scope=self.scope) as store:
            collection, _ = await self._collection(
                store, table_id, configuration, request
            )
            context = window_context(self.role, request, collection, configuration)
        windows = WindowStore(self.scope)
        if request.cursor:
            identifier, window, position = await windows.load(request.cursor, context)
            vector = None
        else:
            async with search_capacity(self.scope) as acquired:
                if not acquired:
                    raise EmbeddingError(EmbeddingErrorCode.RATE_LIMITED)
                embedding_request = EmbeddingRequest(
                    self.scope,
                    configuration.version,
                    spec.dimensions,
                    (
                        EmbeddingInput(
                            0,
                            hashlib.sha256(request.query.encode()).hexdigest(),
                            request.query,
                        ),
                    ),
                )
                async with httpx.AsyncClient() as http:
                    batch = await embed_current(
                        embedding_request, self.client or EmbeddingClient(http)
                    )
                vector = batch.results[0].vector
            identifier = ""
            position = 0
            window = SearchWindow(context=context, references=[], capped=False)
        async with TableSearchSource.with_session(scope=self.scope) as store:
            collection, status = await self._collection(
                store, table_id, configuration, request
            )
            if window_context(self.role, request, collection, configuration) != context:
                raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
            table = await store.table(table_id)
            if vector is not None:
                references, capped = await rank_rows(store, table, collection, vector)
                window = SearchWindow(
                    context=context, references=references, capped=capped
                )
            # Recheck the entire remaining bounded window in one SQL query, then
            # advance over stale references as well as returned rows. No refill.
            results = await read_results(
                store, table, collection, spec.dimensions, window.references[position:]
            )
            items = []
            while position < len(window.references) and len(items) < request.limit:
                ref = window.references[position]
                position += 1
                if item := results.get(str(ref.chunk_id)):
                    items.append(item)
            while (
                position < len(window.references)
                and str(window.references[position].chunk_id) not in results
            ):
                position += 1
        has_more = position < len(window.references)
        if vector is not None:
            # Persist even single-page windows: Redis availability is part of the
            # protocol, never an excuse to silently change pagination semantics.
            identifier = await windows.save(window)
        return SearchPage(
            items=items,
            next_cursor=windows.cursor(identifier, window, position)
            if has_more
            else None,
            has_more=has_more,
            capped=window.capped,
            index=status,
        )
