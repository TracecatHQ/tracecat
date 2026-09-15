"""Transactional storage primitives for semantic search.

Callers own commits. Never retain a transaction across provider calls. All
writers acquire lock_scope BEFORE source-row locks, then access documents and
chunks. The workspace advisory lock also covers not-yet-created collections.
"""

import hashlib
import math
import struct
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Self

import numpy as np
from sqlalchemy import and_, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import (
    SearchChunk,
    SearchCollection,
    SearchDocument,
    SearchEmbeddingConfig,
    SearchWorkspaceState,
    Workspace,
)
from tracecat.db.rls import set_rls_context
from tracecat.search.schemas import SearchIndexStatus
from tracecat.search.types import (
    BuildClaim,
    ChunkerSettings,
    ChunkManifest,
    DocumentState,
    EmbeddingResult,
    EnumerationCursor,
    SearchError,
    SearchErrorCode,
    SearchScope,
    SearchState,
)
from tracecat.service import BaseService


class SearchStorage(BaseService):
    """Scoped persistence; authorization and provider validation belong upstream."""

    service_name = "search_storage"

    def __init__(self, session: AsyncSession, scope: SearchScope):
        super().__init__(session)
        self.scope = scope

    @classmethod
    @asynccontextmanager
    async def with_session(
        cls,
        *,
        scope: SearchScope | None = None,
        session: AsyncSession | None = None,
    ) -> AsyncIterator[Self]:
        """Create scoped storage, closing only sessions owned by this helper.

        Args:
            scope: Required trusted tenant scope; never inferred from context.
            session: Optional caller-owned session. Callers own commits in both
                cases; closing an owned session rolls back uncommitted work.

        Raises:
            ValueError: If no explicit scope is provided.
        """
        if scope is None:
            raise ValueError("SearchStorage requires an explicit tenant scope")
        if session is not None:
            yield cls(session, scope)
        else:
            async with get_async_session_context_manager() as owned_session:
                await set_rls_context(
                    owned_session,
                    org_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    bypass=False,
                )
                yield cls(owned_session, scope)

    async def lock_scope(self) -> None:
        """Lock even an absent collection; prevent source deletion during work."""
        identity = (
            f"semantic-search:{self.scope.organization_id}:{self.scope.workspace_id}"
        )
        key = int.from_bytes(
            hashlib.sha256(identity.encode()).digest()[:8], signed=True
        )
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": key}
        )
        live = await self.session.scalar(
            select(Workspace.id)
            .where(
                Workspace.id == self.scope.workspace_id,
                Workspace.organization_id == self.scope.organization_id,
            )
            .with_for_update(read=True, key_share=True)
        )
        if live is None:
            raise SearchError(SearchErrorCode.NOT_FOUND)

    def _scope(
        self,
        model: type[SearchCollection]
        | type[SearchDocument]
        | type[SearchChunk]
        | type[SearchEmbeddingConfig]
        | type[SearchWorkspaceState],
    ):
        return and_(
            model.organization_id == self.scope.organization_id,
            model.workspace_id == self.scope.workspace_id,
        )

    async def _state(self) -> SearchWorkspaceState:
        state = await self.session.scalar(
            select(SearchWorkspaceState)
            .execution_options(populate_existing=True)
            .where(self._scope(SearchWorkspaceState))
        )
        if state is None:
            state = SearchWorkspaceState(
                organization_id=self.scope.organization_id,
                workspace_id=self.scope.workspace_id,
                current_version=0,
                state=SearchState.DISABLED,
            )
            self.session.add(state)
            await self.session.flush()
        return state

    async def collection(self, collection_id: uuid.UUID) -> SearchCollection:
        """Resolve a collection only in the explicitly trusted tenant scope."""
        await self.lock_scope()
        collection = await self.session.scalar(
            select(SearchCollection)
            .execution_options(populate_existing=True)
            .where(self._scope(SearchCollection), SearchCollection.id == collection_id)
        )
        if collection is None:
            raise SearchError(SearchErrorCode.NOT_FOUND)
        return collection

    async def save_configuration(
        self,
        *,
        provider: str,
        model: str,
        endpoint: str | None,
        credential_id: uuid.UUID,
        credential_environment: str,
        dimensions: int,
        input_token_limit: int,
    ) -> SearchEmbeddingConfig:
        """Persist an upstream-validated config; invalidate old versions atomically.

        The provider service must authorize the workspace credential binding and
        validate the model before calling this primitive. No secrets are stored.
        """
        if not 1 <= dimensions <= 3072 or input_token_limit <= 0:
            raise SearchError(SearchErrorCode.INVALID_VECTOR)
        await self.lock_scope()
        state = await self._state()
        version = state.current_version + 1
        config = SearchEmbeddingConfig(
            organization_id=self.scope.organization_id,
            workspace_id=self.scope.workspace_id,
            version=version,
            provider=provider,
            model=model,
            endpoint=endpoint,
            credential_id=credential_id,
            credential_environment=credential_environment,
            dimensions=dimensions,
            input_token_limit=input_token_limit,
        )
        self.session.add(config)
        state.current_version = version
        state.reconciliation_required = True
        await self.session.flush()
        return config

    async def set_state(self, state: SearchState) -> None:
        """Pause preserves bookkeeping; rollback requires explicit reconstruction."""
        await self.lock_scope()
        current = await self._state()
        if (
            state == SearchState.ACTIVE
            and await self._configuration(current.current_version) is None
        ):
            raise SearchError(SearchErrorCode.INDEX_NOT_READY)
        current.state = state
        if state == SearchState.REINDEX_REQUIRED:
            current.reconciliation_required = True
            # Advance past the old version immediately; resuming requires saving
            # a new configuration and rebuilding each collection against it.
            current.current_version += 1
        await self.session.flush()

    async def _configuration(self, version: int) -> SearchEmbeddingConfig | None:
        return await self.session.scalar(
            select(SearchEmbeddingConfig).where(
                self._scope(SearchEmbeddingConfig),
                SearchEmbeddingConfig.version == version,
            )
        )

    async def configure_collection(
        self,
        *,
        source_id: uuid.UUID,
        column_ids: tuple[uuid.UUID, ...],
        chunker: ChunkerSettings,
        expected_generation: int | None,
    ) -> SearchCollection:
        """Create/update selection after the lifecycle owner validates source columns."""
        await self.lock_scope()
        state = await self._state()
        configuration = await self._configuration(state.current_version)
        if configuration is None or len(set(column_ids)) != len(column_ids):
            raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
        if chunker.overlap_tokens >= chunker.input_tokens:
            raise SearchError(SearchErrorCode.MANIFEST_CONFLICT)
        collection = await self.session.scalar(
            select(SearchCollection)
            .execution_options(populate_existing=True)
            .where(
                self._scope(SearchCollection),
                SearchCollection.source_type == "table",
                SearchCollection.source_id == source_id,
            )
        )
        if collection is None:
            if expected_generation is not None:
                raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
            collection = SearchCollection(
                organization_id=self.scope.organization_id,
                workspace_id=self.scope.workspace_id,
                source_id=source_id,
                generation=1,
                config_version=state.current_version,
            )
            self.session.add(collection)
        else:
            if collection.generation != expected_generation:
                raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
            collection.generation += 1
        collection.config_version = state.current_version
        collection.selected_column_ids = list(column_ids)
        collection.chunker_settings = chunker.model_dump()
        collection.enabled = bool(column_ids)
        collection.deleted_at = None
        collection.backfill_cursor = None
        collection.backfill_complete = False
        await self.session.flush()
        return collection

    async def touch_document(
        self,
        collection_id: uuid.UUID,
        row_id: uuid.UUID,
        *,
        deleted: bool = False,
        backfill: bool = False,
    ) -> SearchDocument:
        """Record a source change in its transaction; backfill never overwrites work."""
        collection = await self.collection(collection_id)
        document = await self.session.scalar(
            select(SearchDocument)
            .execution_options(populate_existing=True)
            .where(
                self._scope(SearchDocument),
                SearchDocument.collection_id == collection_id,
                SearchDocument.source_row_id == row_id,
            )
        )
        if document is None:
            document = SearchDocument(
                organization_id=self.scope.organization_id,
                workspace_id=self.scope.workspace_id,
                collection_id=collection_id,
                source_row_id=row_id,
                desired_revision=1,
                fence=0,
            )
            self.session.add(document)
        elif backfill:
            return document
        else:
            document.desired_revision += 1
            document.fence += 1
        document.generation = collection.generation
        document.build_revision = None
        document.indexed_revision = None
        document.enumeration_cursor = None
        document.enumeration_complete = False
        document.expected_chunks = 0
        document.lease_until = None
        document.next_attempt_at = None
        document.attempts = 0
        document.error_code = None
        document.state = DocumentState.DELETED if deleted else DocumentState.PENDING
        document.deleted_at = (
            await self.session.scalar(select(func.clock_timestamp()))
            if deleted
            else None
        )
        await self.session.flush()
        return document

    async def _document(self, document_id: uuid.UUID) -> SearchDocument:
        document = await self.session.scalar(
            select(SearchDocument)
            .execution_options(populate_existing=True)
            .where(self._scope(SearchDocument), SearchDocument.id == document_id)
        )
        if document is None:
            raise SearchError(SearchErrorCode.NOT_FOUND)
        return document

    async def _active(self, collection: SearchCollection) -> None:
        state = await self._state()
        if (
            state.state != SearchState.ACTIVE
            or not collection.enabled
            or collection.deleted_at is not None
            or collection.config_version != state.current_version
        ):
            raise SearchError(SearchErrorCode.INDEX_NOT_READY)

    async def claim(
        self,
        collection_id: uuid.UUID,
        document_id: uuid.UUID,
        *,
        lease_seconds: int = 60,
    ) -> BuildClaim | None:
        """Acquire a fresh fencing token, retaining checkpoints within a revision."""
        if not 1 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        collection = await self.collection(collection_id)
        await self._active(collection)
        document = await self._document(document_id)
        if document.collection_id != collection_id:
            raise SearchError(SearchErrorCode.NOT_FOUND)
        now = await self.session.scalar(select(func.clock_timestamp()))
        assert now is not None
        if document.deleted_at is not None:
            return None
        if document.generation != collection.generation:
            document = await self.touch_document(collection_id, document.source_row_id)
        if document.state == DocumentState.FAILED and document.next_attempt_at is None:
            return None
        if document.state in (DocumentState.READY, DocumentState.EMPTY):
            return None
        if (document.lease_until is not None and document.lease_until > now) or (
            document.next_attempt_at is not None and document.next_attempt_at > now
        ):
            return None
        document.fence += 1
        document.build_revision = document.desired_revision
        document.lease_until = now + timedelta(seconds=lease_seconds)
        document.state = DocumentState.BUILDING
        document.attempts += 1
        await self.session.flush()
        return BuildClaim(
            collection_id=collection.id,
            document_id=document.id,
            generation=collection.generation,
            config_version=collection.config_version,
            revision=document.desired_revision,
            fence=document.fence,
        )

    async def _fenced(
        self, claim: BuildClaim
    ) -> tuple[SearchCollection, SearchDocument]:
        collection = await self.collection(claim.collection_id)
        await self._active(collection)
        document = await self._document(claim.document_id)
        now = await self.session.scalar(select(func.clock_timestamp()))
        if (
            document.collection_id != collection.id
            or collection.generation != claim.generation
            or collection.config_version != claim.config_version
            or document.generation != claim.generation
            or document.fence != claim.fence
            or document.desired_revision != claim.revision
            or document.build_revision != claim.revision
            or document.state != DocumentState.BUILDING
            or document.deleted_at is not None
            or document.lease_until is None
            or now is None
            or document.lease_until <= now
        ):
            raise SearchError(SearchErrorCode.STALE_CLAIM)
        return collection, document

    async def checkpoint(
        self,
        claim: BuildClaim,
        *,
        before: EnumerationCursor,
        after: EnumerationCursor,
        chunks: tuple[ChunkManifest, ...],
        complete: bool = False,
    ) -> None:
        """Atomically append a contiguous manifest and persist its source cursor."""
        collection, document = await self._fenced(claim)
        current = EnumerationCursor.model_validate(document.enumeration_cursor or {})
        if current == after and document.enumeration_complete == complete:
            existing = (
                await self.session.scalars(
                    select(SearchChunk)
                    .where(
                        self._scope(SearchChunk),
                        SearchChunk.document_id == claim.document_id,
                        SearchChunk.generation == claim.generation,
                        SearchChunk.revision == claim.revision,
                        SearchChunk.ordinal >= before.next_ordinal,
                        SearchChunk.ordinal < after.next_ordinal,
                    )
                    .order_by(SearchChunk.ordinal)
                )
            ).all()
            recorded = tuple(
                ChunkManifest(
                    ordinal=chunk.ordinal,
                    column_id=chunk.column_id,
                    column_name=chunk.column_name,
                    start=chunk.start_offset,
                    end=chunk.end_offset,
                    input_hash=chunk.input_hash,
                )
                for chunk in existing
            )
            if recorded == chunks:
                return
            raise SearchError(SearchErrorCode.MANIFEST_CONFLICT)
        if document.enumeration_complete or current != before:
            raise SearchError(SearchErrorCode.MANIFEST_CONFLICT)
        if len(chunks) > 32 or after.next_ordinal != before.next_ordinal + len(chunks):
            raise SearchError(SearchErrorCode.MANIFEST_CONFLICT)
        if (after.column_index, after.character_offset) < (
            before.column_index,
            before.character_offset,
        ):
            raise SearchError(SearchErrorCode.MANIFEST_CONFLICT)
        config = await self.session.scalar(
            select(SearchEmbeddingConfig).where(
                self._scope(SearchEmbeddingConfig),
                SearchEmbeddingConfig.version == claim.config_version,
            )
        )
        assert config is not None
        for offset, chunk in enumerate(chunks):
            if (
                chunk.ordinal != before.next_ordinal + offset
                or chunk.end <= chunk.start
                or chunk.column_id not in collection.selected_column_ids
            ):
                raise SearchError(SearchErrorCode.MANIFEST_CONFLICT)
        # Callers may catch a validation error and commit their transaction.
        # Stage nothing until the entire batch has passed validation.
        for chunk in chunks:
            self.session.add(
                SearchChunk(
                    organization_id=self.scope.organization_id,
                    workspace_id=self.scope.workspace_id,
                    collection_id=collection.id,
                    document_id=document.id,
                    generation=claim.generation,
                    revision=claim.revision,
                    config_version=claim.config_version,
                    dimensions=config.dimensions,
                    ordinal=chunk.ordinal,
                    column_id=chunk.column_id,
                    column_name=chunk.column_name,
                    start_offset=chunk.start,
                    end_offset=chunk.end,
                    input_hash=chunk.input_hash,
                )
            )
        document.enumeration_cursor = after.model_dump()
        document.enumeration_complete = complete
        document.expected_chunks = after.next_ordinal
        await self.session.flush()

    async def write_embeddings(
        self, claim: BuildClaim, results: tuple[EmbeddingResult, ...]
    ) -> None:
        """Persist a bounded provider response only if its claim is still current."""
        await self._fenced(claim)
        if len(results) > 32 or len({result.ordinal for result in results}) != len(
            results
        ):
            raise SearchError(SearchErrorCode.INVALID_VECTOR)
        chunks = (
            await self.session.scalars(
                select(SearchChunk).where(
                    self._scope(SearchChunk),
                    SearchChunk.document_id == claim.document_id,
                    SearchChunk.generation == claim.generation,
                    SearchChunk.revision == claim.revision,
                    SearchChunk.ordinal.in_([result.ordinal for result in results]),
                )
            )
        ).all()
        by_ordinal = {chunk.ordinal: chunk for chunk in chunks}
        for result in results:
            chunk = by_ordinal.get(result.ordinal)
            if (
                chunk is None
                or result.config_version != claim.config_version
                or chunk.input_hash != result.input_hash
                or len(result.vector) != chunk.dimensions
                or not all(math.isfinite(value) for value in result.vector)
                or not any(value != 0 for value in result.vector)
            ):
                raise SearchError(SearchErrorCode.INVALID_VECTOR)
            try:
                vector = [
                    struct.unpack("!f", struct.pack("!f", value))[0]
                    for value in result.vector
                ]
            except OverflowError:
                raise SearchError(SearchErrorCode.INVALID_VECTOR) from None
            if not any(value != 0 for value in vector):
                raise SearchError(SearchErrorCode.INVALID_VECTOR)
            if chunk.state == "embedded":
                continue  # Retries cannot inflate progress or replace completed vectors.
            chunk.embedding = np.asarray(vector, dtype=np.float32)
            chunk.state = "embedded"
            chunk.error_code = None
        await self.session.flush()

    async def publish(self, claim: BuildClaim) -> None:
        """Publish only a complete contiguous embedded manifest, never a counter."""
        collection = await self.collection(claim.collection_id)
        await self._active(collection)
        published = await self._document(claim.document_id)
        if (
            published.collection_id == claim.collection_id
            and published.state in (DocumentState.READY, DocumentState.EMPTY)
            and published.indexed_revision
            == published.desired_revision
            == claim.revision
            and published.fence == claim.fence
            and published.generation == collection.generation == claim.generation
            and collection.config_version == claim.config_version
        ):
            return
        _, document = await self._fenced(claim)
        count, embedded, minimum, maximum = (
            await self.session.execute(
                select(
                    func.count(),
                    func.count().filter(SearchChunk.state == "embedded"),
                    func.min(SearchChunk.ordinal),
                    func.max(SearchChunk.ordinal),
                ).where(
                    self._scope(SearchChunk),
                    SearchChunk.document_id == claim.document_id,
                    SearchChunk.generation == claim.generation,
                    SearchChunk.revision == claim.revision,
                    SearchChunk.config_version == claim.config_version,
                )
            )
        ).one()
        if (
            not document.enumeration_complete
            or count != document.expected_chunks
            or embedded != count
            or (count > 0 and (minimum != 0 or maximum != count - 1))
        ):
            raise SearchError(SearchErrorCode.INDEX_NOT_READY)
        document.indexed_revision = claim.revision
        document.state = DocumentState.READY if count else DocumentState.EMPTY
        document.lease_until = None
        document.error_code = None
        await self.session.flush()

    async def fail(
        self,
        claim: BuildClaim,
        code: SearchErrorCode,
        *,
        retry_seconds: int | None = None,
    ) -> None:
        """Persist only a typed, sanitized error; release the lease for retry."""
        _, document = await self._fenced(claim)
        now = await self.session.scalar(select(func.clock_timestamp()))
        assert now is not None
        document.state = DocumentState.FAILED
        document.error_code = code.value
        document.lease_until = None
        document.next_attempt_at = (
            now + timedelta(seconds=max(0, retry_seconds))
            if retry_seconds is not None
            else None
        )
        await self.session.flush()

    async def tombstone_collection(self, collection_id: uuid.UUID) -> None:
        """Invalidate a deleted source in O(1); its chunks remain ineligible."""
        collection = await self.collection(collection_id)
        collection.enabled = False
        collection.generation += 1
        collection.deleted_at = await self.session.scalar(
            select(func.clock_timestamp())
        )
        await self.session.flush()

    async def cleanup_chunks(
        self, collection_id: uuid.UUID, *, limit: int = 1000
    ) -> int:
        """Delete only stale/tombstoned chunks, bounded independently of row size."""
        if not 1 <= limit <= 1000:
            raise ValueError("cleanup limit must be between 1 and 1000")
        collection = await self.collection(collection_id)
        stale = (
            select(SearchChunk.id)
            .join(SearchDocument, SearchDocument.id == SearchChunk.document_id)
            .where(
                self._scope(SearchChunk),
                SearchChunk.collection_id == collection.id,
                (SearchChunk.generation != collection.generation)
                | (SearchChunk.revision != SearchDocument.desired_revision)
                | (SearchDocument.deleted_at.is_not(None))
                | (collection.deleted_at is not None),
            )
            .order_by(SearchChunk.id)
            .limit(limit)
        )
        removed = (
            await self.session.scalars(
                delete(SearchChunk)
                .where(SearchChunk.id.in_(stale))
                .returning(SearchChunk.id)
            )
        ).all()
        return len(removed)

    async def checkpoint_backfill(
        self,
        collection_id: uuid.UUID,
        *,
        generation: int,
        before: uuid.UUID | None,
        after: uuid.UUID | None,
        complete: bool = False,
    ) -> None:
        """Advance a source keyset cursor in the same transaction as row discovery."""
        collection = await self.collection(collection_id)
        await self._active(collection)
        if collection.generation != generation:
            raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
        if (
            collection.backfill_cursor == after
            and collection.backfill_complete == complete
        ):
            return
        if collection.backfill_cursor != before or collection.backfill_complete:
            raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
        if before is not None and (after is None or after.int < before.int):
            raise SearchError(SearchErrorCode.MANIFEST_CONFLICT)
        collection.backfill_cursor = after
        collection.backfill_complete = complete
        await self.session.flush()

    async def retry_document(
        self, collection_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        """Explicitly retry failed work without discarding already embedded chunks."""
        collection = await self.collection(collection_id)
        await self._active(collection)
        document = await self._document(document_id)
        if (
            document.collection_id != collection_id
            or document.state != DocumentState.FAILED
        ):
            raise SearchError(SearchErrorCode.NOT_FOUND)
        document.state = DocumentState.PENDING
        document.next_attempt_at = None
        document.error_code = None
        document.attempts = 0
        await self.session.flush()

    async def status(self, collection_id: uuid.UUID) -> SearchIndexStatus:
        """Summarize stored work; unenumerated source rows are covered by backfill."""
        collection = await self.collection(collection_id)
        state = await self._state()
        current_generation = SearchDocument.generation == collection.generation
        current = current_generation & (
            SearchDocument.indexed_revision == SearchDocument.desired_revision
        )
        total, ready, empty, failed = (
            await self.session.execute(
                select(
                    func.count(),
                    func.count().filter(current & (SearchDocument.state == "ready")),
                    func.count().filter(current & (SearchDocument.state == "empty")),
                    func.count().filter(
                        current_generation & (SearchDocument.state == "failed")
                    ),
                ).where(
                    self._scope(SearchDocument),
                    SearchDocument.collection_id == collection.id,
                    SearchDocument.deleted_at.is_(None),
                )
            )
        ).one()
        configuration_changed = collection.config_version != state.current_version
        if configuration_changed:
            ready = empty = failed = 0
        pending = total - ready - empty - failed
        unavailable = (
            state.state != SearchState.ACTIVE
            or configuration_changed
            or not collection.enabled
            or collection.deleted_at is not None
        )
        return SearchIndexStatus(
            state=SearchState(state.state)
            if collection.enabled
            else SearchState.DISABLED,
            pending=pending,
            ready=ready,
            empty=empty,
            failed=failed,
            backfill_complete=collection.backfill_complete,
            partial=unavailable
            or not collection.backfill_complete
            or pending > 0
            or failed > 0,
        )
