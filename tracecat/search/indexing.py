"""Bounded indexing work; transactions never cross embedding calls."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

import sqlalchemy as sa

from tracecat.db.models import SearchChunk, SearchCollection, SearchDocument
from tracecat.search.chunking import TextChunker
from tracecat.search.chunking_types import (
    ChunkCheckpoint,
    ChunkingConfig,
    ChunkingError,
    ChunkingIdentity,
    ChunkReference,
    TextColumn,
)
from tracecat.search.embeddings.catalog import token_counter
from tracecat.search.embeddings.types import (
    EmbeddingBatch,
    EmbeddingError,
    PinnedConfiguration,
)
from tracecat.search.indexing_types import (
    CollectionWork,
    IndexingOutcome,
    IndexingProgress,
)
from tracecat.search.types import (
    BuildClaim,
    ChunkerSettings,
    ChunkManifest,
    EmbeddingInput,
    EmbeddingRequest,
    SearchError,
    SearchErrorCode,
    SearchState,
    decode_enumeration_cursor,
)
from tracecat.tables.search.source import TableSearchSource

Embed = Callable[[EmbeddingRequest], Awaitable[EmbeddingBatch]]


def settings_for(configuration: PinnedConfiguration) -> ChunkerSettings:
    """Pin every preparation setting, including the provider's budget."""
    budget = min(
        800, configuration.spec.input_token_limit, configuration.spec.batch_token_limit
    )
    return ChunkerSettings(
        tokenizer=token_counter(configuration.spec).identity,
        input_tokens=budget,
        overlap_tokens=min(128, budget // 4),
        provider_input_tokens=configuration.spec.input_token_limit,
        read_size=min(4096, configuration.spec.input_character_limit),
    )


def make_chunker(
    source: TableSearchSource,
    claim: BuildClaim,
    settings: ChunkerSettings,
    columns: list[TextColumn],
    configuration: PinnedConfiguration,
) -> TextChunker:
    return TextChunker(
        identity=ChunkingIdentity(
            organization_id=source.scope.organization_id,
            workspace_id=source.scope.workspace_id,
            collection_id=claim.collection_id,
            document_id=claim.document_id,
            generation=claim.generation,
            revision=claim.revision,
            config_version=claim.config_version,
        ),
        columns=columns,
        config=ChunkingConfig.model_validate(settings.model_dump()),
        tokenizer=token_counter(configuration.spec),
    )


async def cleanup_collection(source: TableSearchSource, collection_id: UUID) -> int:
    """Remove bounded stale chunks and then childless deleted documents."""
    removed = await source.cleanup_chunks(collection_id)
    ids = (
        sa.select(SearchDocument.id)
        .where(
            source._scope(SearchDocument),
            SearchDocument.collection_id == collection_id,
            SearchDocument.deleted_at.is_not(None),
            ~sa.exists(
                sa.select(SearchChunk.id).where(
                    SearchChunk.document_id == SearchDocument.id
                )
            ),
        )
        .order_by(SearchDocument.id)
        .limit(100)
    )
    removed += len(
        (
            await source.session.scalars(
                sa.delete(SearchDocument)
                .where(SearchDocument.id.in_(ids))
                .returning(SearchDocument.id)
            )
        ).all()
    )
    return removed


async def record_failure(
    work: CollectionWork,
    claim: BuildClaim,
    exc: EmbeddingError | ChunkingError | SearchError,
) -> None:
    """Keep good chunks and use durable retries, without storing exception text."""
    try:
        async with TableSearchSource.with_session(scope=work.scope) as source:
            _, doc = await source._fenced(claim)
            retry = None
            if isinstance(exc, EmbeddingError) and exc.retryable and doc.attempts < 5:
                retry = min(300, max(2**doc.attempts, int(exc.retry_after or 0)))
            code = (
                exc.code
                if isinstance(exc, (SearchError, EmbeddingError))
                else SearchErrorCode.MANIFEST_CONFLICT
            )
            await source.fail(claim, code, retry_seconds=retry)
            await source.session.commit()
    except SearchError:
        # An edit, expired lease or new configuration owns the work now.
        return


async def _claim_next(
    work: CollectionWork,
    configuration: PinnedConfiguration | None,
    progress: IndexingProgress,
) -> BuildClaim | None:
    """Reconcile configuration, checkpoint backfill, and lease the oldest work."""
    async with TableSearchSource.with_session(scope=work.scope) as source:
        collection = await source.collection(work.collection_id)
        progress.cleaned = await cleanup_collection(source, collection.id)
        if not collection.enabled or collection.deleted_at is not None:
            await source.session.commit()
            return None
        state = await source._state()
        if configuration is None or state.state == SearchState.PAUSED:
            progress.outcome = (
                IndexingOutcome.UNAVAILABLE
                if configuration is None
                else IndexingOutcome.PAUSED
            )
            await source.session.commit()
            return None
        if configuration.version != state.current_version:
            raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
        settings = await asyncio.to_thread(settings_for, configuration)
        if (
            collection.config_version != configuration.version
            or collection.chunker_settings != settings.model_dump()
        ):
            collection = await source.configure_collection(
                source_id=collection.source_id,
                column_ids=tuple(collection.selected_column_ids),
                chunker=settings,
                expected_generation=collection.generation,
            )
        stale = await source.session.scalar(
            sa.select(
                sa.exists().where(
                    source._scope(SearchCollection),
                    SearchCollection.enabled.is_(True),
                    SearchCollection.deleted_at.is_(None),
                    SearchCollection.config_version.is_distinct_from(
                        configuration.version
                    ),
                )
            )
        )
        if not stale:
            state.reconciliation_required = False
        if not collection.backfill_complete:
            before = collection.backfill_cursor
            page = await source.scan_rows(
                collection.id, generation=collection.generation, after=before
            )
            await source.touch_documents(
                collection.id, [row.row_id for row in page.rows], backfill=True
            )
            await source.checkpoint_backfill(
                collection.id,
                generation=collection.generation,
                before=before,
                after=page.rows[-1].row_id if page.rows else before,
                complete=not page.has_more,
            )
            progress.discovered = len(page.rows)
        claimed = await source.claim_next_due(collection.id)
        await source.session.commit()
        # Telemetry observes a bounded sample after releasing the workspace lock.
        sample = await source.sample_backlog(collection.id)
        progress.pending, progress.failed = sample.pending, sample.failed
        if claimed is not None:
            progress.queue_wait_seconds = claimed.queue_wait_seconds
    return claimed.claim if claimed is not None else None


async def _prepare_inputs(
    work: CollectionWork,
    claim: BuildClaim,
    configuration: PinnedConfiguration,
    progress: IndexingProgress,
) -> tuple[EmbeddingInput, ...]:
    """Persist enumeration and reuse fresh text, reconstructing only resumed work."""
    async with TableSearchSource.with_session(scope=work.scope) as source:
        collection, document = await source._fenced(claim)
        table = await source.table(collection.source_id)
        settings = await asyncio.to_thread(settings_for, configuration)
        chunker = await asyncio.to_thread(
            make_chunker,
            source,
            claim,
            settings,
            [
                TextColumn(UUID(str(c.id)), c.name)
                for c in table.columns
                if c.id in collection.selected_column_ids
            ],
            configuration,
        )
        before = decode_enumeration_cursor(document.enumeration_cursor)
        if document.enumeration_cursor and not isinstance(before, ChunkCheckpoint):
            raise SearchError(SearchErrorCode.MANIFEST_CONFLICT)
        pending = (
            sa.select(SearchChunk)
            .where(
                source._scope(SearchChunk),
                SearchChunk.document_id == claim.document_id,
                SearchChunk.generation == claim.generation,
                SearchChunk.revision == claim.revision,
                SearchChunk.state != "embedded",
            )
            .order_by(SearchChunk.ordinal)
            .limit(min(32, configuration.spec.batch_size_limit))
        )
        chunks = (await source.session.scalars(pending)).all()
        batch = None
        if not chunks and not document.enumeration_complete:
            batch = await chunker.prepare_batch(
                source, before if isinstance(before, ChunkCheckpoint) else None
            )
            await source.checkpoint(
                claim,
                before=before,
                after=batch.checkpoint,
                chunks=tuple(
                    ChunkManifest(
                        ordinal=c.metadata.ordinal,
                        column_id=c.metadata.column_id,
                        column_name=c.metadata.column_name,
                        start=c.metadata.start,
                        end=c.metadata.end,
                        input_hash=c.metadata.input_hash,
                    )
                    for c in batch.chunks
                ),
                complete=batch.complete,
            )
            progress.prepared = len(batch.chunks)
        inputs: list[EmbeddingInput] = []
        units = 0

        async def candidates() -> AsyncIterator[tuple[EmbeddingInput, int]]:
            if batch is not None:
                for chunk in batch.chunks:
                    metadata = chunk.metadata
                    yield (
                        EmbeddingInput(
                            metadata.ordinal, metadata.input_hash, chunk.text
                        ),
                        metadata.token_count,
                    )
                return
            counter = await asyncio.to_thread(token_counter, configuration.spec)
            for saved in chunks:
                text = await chunker.reconstruct_input(
                    source,
                    ChunkReference(
                        identity=chunker.identity,
                        config_hash=chunker.initial_checkpoint().config_hash,
                        column_id=saved.column_id,
                        column_name=saved.column_name,
                        ordinal=saved.ordinal,
                        start=saved.start_offset,
                        end=saved.end_offset,
                        input_hash=saved.input_hash,
                    ),
                )
                yield (
                    EmbeddingInput(saved.ordinal, saved.input_hash, text),
                    await asyncio.to_thread(counter.count_tokens, text),
                )

        async for item, count in candidates():
            if units + count > configuration.spec.batch_token_limit:
                break
            inputs.append(item)
            units += count
            if len(inputs) == min(32, configuration.spec.batch_size_limit):
                break
        await source.session.commit()
    return tuple(inputs)


async def _finish_batch(
    work: CollectionWork,
    claim: BuildClaim,
    result: EmbeddingBatch | None,
    progress: IndexingProgress,
) -> None:
    """Save embeddings and publish atomically, or yield incomplete work."""
    async with TableSearchSource.with_session(scope=work.scope) as source:
        if result is not None:
            await source.write_embeddings(claim, result.results)
            progress.embedded = len(result.results)
            progress.prompt_tokens, progress.total_tokens = (
                result.prompt_tokens,
                result.total_tokens,
            )
        progress.outcome = IndexingOutcome(await source.finish_or_yield(claim))
        await source.session.commit()


async def index_collection(
    work: CollectionWork, configuration: PinnedConfiguration | None, embed: Embed
) -> IndexingProgress:
    """Backfill one page and advance one document by one bounded work unit."""
    progress = IndexingProgress(outcome=IndexingOutcome.IDLE)
    claim: BuildClaim | None = None
    try:
        claim = await _claim_next(work, configuration, progress)
        if claim is None:
            return progress
        assert configuration is not None
        inputs = await _prepare_inputs(work, claim, configuration, progress)
        # Text/vectors stay inside the activity. No database connection is held.
        result = (
            await embed(
                EmbeddingRequest(
                    work.scope,
                    configuration.version,
                    configuration.spec.dimensions,
                    inputs,
                )
            )
            if inputs
            else None
        )
        await _finish_batch(work, claim, result, progress)
    except (EmbeddingError, ChunkingError, SearchError) as exc:
        if claim is not None:
            await record_failure(work, claim, exc)
        progress.outcome = (
            exc.code
            if isinstance(exc, (EmbeddingError, SearchError))
            else SearchErrorCode.MANIFEST_CONFLICT
        )
    return progress
