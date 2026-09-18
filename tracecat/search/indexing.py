"""Bounded indexing work; transactions never cross embedding calls."""

from collections.abc import Awaitable, Callable
from uuid import UUID

import orjson
import sqlalchemy as sa

from tracecat.db.models import SearchChunk, SearchCollection, SearchDocument
from tracecat.search.chunking import TextChunker
from tracecat.search.chunking_types import (
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
from tracecat.search.indexing_types import CollectionWork, IndexingProgress
from tracecat.search.types import (
    BuildClaim,
    ChunkerSettings,
    ChunkManifest,
    EmbeddingInput,
    EmbeddingRequest,
    EnumerationCursor,
    SearchError,
    SearchErrorCode,
    SearchState,
)
from tracecat.tables.search_source import TableSearchSource

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
            progress.outcome = "unavailable" if configuration is None else "paused"
            await source.session.commit()
            return None
        if configuration.version != state.current_version:
            raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
        settings = settings_for(configuration)
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
        status = await source.status(collection.id)
        progress.pending, progress.failed = status.pending, status.failed
        # Oldest touched document first; successful batches move to the back.
        due = sa.and_(
            SearchDocument.state.in_(["pending", "building", "failed"]),
            sa.or_(
                SearchDocument.state != "failed",
                SearchDocument.next_attempt_at.is_not(None),
            ),
            sa.or_(
                SearchDocument.next_attempt_at.is_(None),
                SearchDocument.next_attempt_at <= sa.func.now(),
            ),
        )
        document_id = await source.session.scalar(
            sa.select(SearchDocument.id)
            .where(
                source._scope(SearchDocument),
                SearchDocument.collection_id == collection.id,
                SearchDocument.deleted_at.is_(None),
                sa.or_(SearchDocument.generation != collection.generation, due),
                sa.or_(
                    SearchDocument.lease_until.is_(None),
                    SearchDocument.lease_until <= sa.func.now(),
                ),
            )
            .order_by(SearchDocument.updated_at, SearchDocument.id)
            .limit(1)
        )
        if document_id is not None:
            waited = await source.session.scalar(
                sa.select(
                    sa.func.extract(
                        "epoch",
                        sa.func.clock_timestamp() - SearchDocument.updated_at,
                    )
                ).where(SearchDocument.id == document_id)
            )
            progress.queue_wait_seconds = max(0, float(waited or 0))
        claim = (
            await source.claim(collection.id, document_id, lease_seconds=120)
            if document_id
            else None
        )
        await source.session.commit()
    return claim


async def _prepare_inputs(
    work: CollectionWork,
    claim: BuildClaim,
    configuration: PinnedConfiguration,
    progress: IndexingProgress,
) -> tuple[EmbeddingInput, ...]:
    """Persist full enumeration progress and reconstruct a bounded provider batch."""
    async with TableSearchSource.with_session(scope=work.scope) as source:
        collection, document = await source._fenced(claim)
        table = await source.table(collection.source_id)
        chunker = make_chunker(
            source,
            claim,
            settings_for(configuration),
            [
                TextColumn(UUID(str(c.id)), c.name)
                for c in table.columns
                if c.id in collection.selected_column_ids
            ],
            configuration,
        )
        before = EnumerationCursor.model_validate_json(
            orjson.dumps(document.enumeration_cursor or {})
        )
        if document.enumeration_cursor and before.chunker is None:
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
        if not chunks and not document.enumeration_complete:
            batch = await chunker.prepare_batch(source, before.chunker)
            await source.checkpoint(
                claim,
                before=before,
                after=EnumerationCursor(
                    column_index=batch.checkpoint.column_index,
                    character_offset=batch.checkpoint.character_offset,
                    next_ordinal=batch.checkpoint.next_ordinal,
                    chunker=batch.checkpoint,
                ),
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
            chunks = (await source.session.scalars(pending)).all()
        inputs: list[EmbeddingInput] = []
        units = 0
        counter = token_counter(configuration.spec)
        for chunk in chunks:
            text = await chunker.reconstruct_input(
                source,
                ChunkReference(
                    identity=chunker.identity,
                    config_hash=chunker.initial_checkpoint().config_hash,
                    column_id=chunk.column_id,
                    column_name=chunk.column_name,
                    ordinal=chunk.ordinal,
                    start=chunk.start_offset,
                    end=chunk.end_offset,
                    input_hash=chunk.input_hash,
                ),
            )
            count = counter.count_tokens(text)
            if units + count > configuration.spec.batch_token_limit:
                break
            inputs.append(EmbeddingInput(chunk.ordinal, chunk.input_hash, text))
            units += count
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
        _, document = await source._fenced(claim)
        if result is not None:
            await source.write_embeddings(claim, result.results)
            progress.embedded = len(result.results)
            progress.prompt_tokens, progress.total_tokens = (
                result.prompt_tokens,
                result.total_tokens,
            )
        missing = await source.session.scalar(
            sa.select(
                sa.exists().where(
                    source._scope(SearchChunk),
                    SearchChunk.document_id == claim.document_id,
                    SearchChunk.generation == claim.generation,
                    SearchChunk.revision == claim.revision,
                    SearchChunk.state != "embedded",
                )
            )
        )
        if document.enumeration_complete and not missing:
            await source.publish(claim)
            progress.outcome = "published"
        else:
            await source.yield_claim(claim)
            progress.outcome = "progress"
        await source.session.commit()


async def index_collection(
    work: CollectionWork, configuration: PinnedConfiguration | None, embed: Embed
) -> IndexingProgress:
    """Backfill one page and advance one document by one bounded work unit."""
    progress = IndexingProgress(outcome="idle")
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
            exc.code.value
            if isinstance(exc, (EmbeddingError, SearchError))
            else "MANIFEST_CONFLICT"
        )
    return progress
