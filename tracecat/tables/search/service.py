"""Table-specific selection and transactional source lifecycle.

Every operation uses the caller's transaction. Never resolve provider credentials
or perform external work here. The workspace lock precedes all source writes,
even while no collection exists, so enabling cannot miss a concurrent writer.
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import aliased, selectinload

from tracecat.db.models import (
    SearchChunk,
    SearchCollection,
    SearchDocument,
    Table,
    TableColumn,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.search.embeddings.schemas import EmbeddingConfigurationRead
from tracecat.search.service import SearchStorage
from tracecat.search.types import (
    ChunkerSettings,
    DocumentState,
    SearchError,
    SearchErrorCode,
    SearchState,
)
from tracecat.tables.enums import SqlType
from tracecat.tables.search.schemas import (
    TableSearchConfiguration,
    TableSearchDisplayState,
    TableSearchDocumentProgress,
    TableSearchProgressPage,
    TableSearchSelection,
)


class TableSearchService(SearchStorage):
    """Small source hooks shared by public, internal, import and sync writers."""

    async def for_table(self, table_id: UUID) -> SearchCollection | None:
        """Lock before inspecting even an absent selection, then refresh metadata."""
        await self.lock_scope()
        return await self.session.scalar(
            sa.select(SearchCollection)
            .where(
                self._scope(SearchCollection),
                SearchCollection.source_id == table_id,
                SearchCollection.source_type == "table",
            )
            .execution_options(populate_existing=True)
        )

    async def table(self, table_id: UUID) -> Table:
        """Resolve live source metadata in the trusted workspace."""
        table = await self.session.scalar(
            sa.select(Table)
            .where(Table.id == table_id, Table.workspace_id == self.scope.workspace_id)
            .options(selectinload(Table.columns))
            .execution_options(populate_existing=True)
        )
        if table is None:
            raise TracecatNotFoundError("Table not found")
        return table

    async def select_column(
        self, table_id: UUID, params: TableSearchSelection
    ) -> SearchCollection | None:
        """Apply an idempotent selection change with optimistic concurrency."""
        collection = await self.for_table(table_id)
        table = await self.table(table_id)
        generation = collection.generation if collection else 0
        if generation != params.expected_generation:
            raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
        column = next((c for c in table.columns if c.id == params.column_id), None)
        if column is None:
            raise TracecatNotFoundError("Column not found")
        if params.enabled and column.type != SqlType.TEXT:
            raise ValueError("Only TEXT columns support semantic search")
        selected = set(collection.selected_column_ids if collection else [])
        if (params.column_id in selected) == params.enabled:
            return collection
        if params.enabled:
            selected.add(params.column_id)
        else:
            selected.discard(params.column_id)
        if collection is None:
            collection = SearchCollection(
                organization_id=self.scope.organization_id,
                workspace_id=self.scope.workspace_id,
                source_id=table_id,
                generation=1,
                config_version=None,
                chunker_settings=ChunkerSettings(tokenizer="unconfigured").model_dump(),
            )
            self.session.add(collection)
        else:
            collection.generation += 1
        # Selection never binds a new provider version to old tokenizer settings.
        # The worker reconciles unbound/stale collections using configure_collection.
        collection.selected_column_ids = sorted(selected)
        collection.enabled = bool(selected)
        collection.deleted_at = None
        collection.backfill_cursor = None
        collection.backfill_complete = False
        await self.session.flush()
        return collection

    async def record_rows(
        self,
        collection: SearchCollection | None,
        row_ids: Sequence[UUID],
        *,
        deleted: bool = False,
    ) -> None:
        """Invalidate affected rows in bounded batches without loading text."""
        if (
            collection is None
            or (not collection.enabled and not deleted)
            or collection.deleted_at is not None
            or not row_ids
        ):
            return
        await self.touch_documents(collection.id, row_ids, deleted=deleted)

    @staticmethod
    def selected_names(collection: SearchCollection | None, table: Table) -> set[str]:
        """Resolve stable selection IDs against current source labels."""
        if (
            collection is None
            or not collection.enabled
            or collection.deleted_at is not None
        ):
            return set()
        return {c.name for c in table.columns if c.id in collection.selected_column_ids}

    async def column_changed(
        self,
        collection: SearchCollection | None,
        column: TableColumn,
        *,
        removed: bool = False,
    ) -> None:
        """Rebuild labels or remove invalid selections in the source transaction."""
        if collection is None or column.id not in collection.selected_column_ids:
            return
        selected = list(collection.selected_column_ids)
        if removed or column.type != SqlType.TEXT:
            selected.remove(column.id)
        collection.selected_column_ids = selected
        collection.enabled = bool(selected)
        collection.generation += 1
        collection.backfill_cursor = None
        collection.backfill_complete = False
        await self.session.flush()

    async def configuration(
        self,
        table_id: UUID,
        *,
        availability: EmbeddingConfigurationRead | None = None,
        provider_error: bool = False,
    ) -> TableSearchConfiguration:
        """Combine persisted progress with an optional independently read provider status."""
        collection = await self.for_table(table_id)
        await self.table(table_id)
        if collection is None:
            return TableSearchConfiguration()
        index = await self.status(collection.id)
        if not collection.enabled:
            display = TableSearchDisplayState.DISABLED
        elif index.state != SearchState.ACTIVE or collection.config_version is None:
            display = TableSearchDisplayState.UNAVAILABLE
        elif index.failed:
            display = TableSearchDisplayState.NEEDS_ATTENTION
        elif not index.backfill_complete:
            display = TableSearchDisplayState.INDEXING
        elif index.partial:
            display = TableSearchDisplayState.UPDATING
        else:
            display = TableSearchDisplayState.READY
        if collection.enabled:
            if provider_error:
                display = TableSearchDisplayState.NEEDS_ATTENTION
            elif availability is not None:
                if not availability.available:
                    display = TableSearchDisplayState.UNAVAILABLE
                elif availability.state == SearchState.PAUSED:
                    display = TableSearchDisplayState.UNAVAILABLE
                elif availability.reindex_required or collection.config_version is None:
                    display = (
                        TableSearchDisplayState.INDEXING
                        if not index.backfill_complete
                        else TableSearchDisplayState.UPDATING
                    )
            if display != TableSearchDisplayState.READY:
                index.partial = True
        return TableSearchConfiguration(
            generation=collection.generation,
            selected_column_ids=collection.selected_column_ids,
            status=display,
            index=index,
        )

    async def retry(
        self, table_id: UUID, generation: int, document_ids: Sequence[UUID]
    ) -> None:
        """Retry only explicitly selected failed documents in this table generation."""
        collection = await self.for_table(table_id)
        await self.table(table_id)
        if collection is None or collection.generation != generation:
            raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
        for document_id in set(document_ids):
            document = await self._document(document_id)
            if document.generation != generation:
                raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
            await self.retry_document(collection.id, document_id)

    async def progress(
        self,
        table_id: UUID,
        *,
        generation: int,
        cursor: UUID | None = None,
        limit: int = 20,
    ) -> TableSearchProgressPage:
        """Read at most 100 documents and 1,001 chunk states per document.

        Counts are explicitly a bounded sample, never an invented completion
        percentage. The final expected total is available after enumeration.
        """
        if not 1 <= limit <= 100:
            raise ValueError("Progress limit must be between 1 and 100")
        collection = await self.for_table(table_id)
        await self.table(table_id)
        if collection is None or collection.generation != generation:
            raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
        docs = (
            sa.select(SearchDocument)
            .where(
                self._scope(SearchDocument),
                SearchDocument.collection_id == collection.id,
                SearchDocument.deleted_at.is_(None),
            )
            .order_by(SearchDocument.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            docs = docs.where(SearchDocument.id > cursor)
        doc = aliased(SearchDocument, docs.subquery())
        sample = (
            sa.select(SearchChunk.state)
            .where(
                self._scope(SearchChunk),
                SearchChunk.document_id == doc.id,
                SearchChunk.generation == collection.generation,
                SearchChunk.revision == doc.desired_revision,
            )
            .order_by(SearchChunk.ordinal)
            .limit(1001)
            .correlate(doc)
            .lateral()
        )
        counts = (
            sa.select(
                sa.func.count().label("prepared"),
                sa.func.count().filter(sample.c.state == "embedded").label("embedded"),
            )
            .select_from(sample)
            .lateral()
        )
        rows = (
            await self.session.execute(
                sa.select(doc, counts.c.prepared, counts.c.embedded)
                .join(counts, sa.true())
                .order_by(doc.id)
            )
        ).all()
        items = [
            TableSearchDocumentProgress(
                document_id=d.id,
                row_id=d.source_row_id,
                state=DocumentState(d.state)
                if d.generation == generation
                else DocumentState.PENDING,
                revision=d.desired_revision,
                expected_chunks=d.expected_chunks
                if d.enumeration_complete and d.generation == generation
                else None,
                sampled_chunks=prepared,
                sampled_embedded=embedded,
                chunks_capped=prepared > 1000,
                error_code=d.error_code if d.generation == generation else None,
            )
            for d, prepared, embedded in rows[:limit]
        ]
        return TableSearchProgressPage(
            generation=generation,
            items=items,
            next_cursor=items[-1].document_id if len(rows) > limit else None,
            has_more=len(rows) > limit,
        )
