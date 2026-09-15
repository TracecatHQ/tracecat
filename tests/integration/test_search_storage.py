"""Live PostgreSQL tests for the shared semantic indexing storage boundary."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.db.engine import get_async_engine, reset_async_engine
from tracecat.db.models import (
    Organization,
    SearchChunk,
    SearchCollection,
    SearchDocument,
    SearchEmbeddingConfig,
    SearchWorkspaceState,
    Table,
    Workspace,
)
from tracecat.db.tenant_rls import enable_search_table_rls
from tracecat.search.cleanup import cleanup_orphans
from tracecat.search.query import eligible_chunks
from tracecat.search.service import SearchStorage
from tracecat.search.types import (
    BuildClaim,
    ChunkerSettings,
    ChunkManifest,
    EmbeddingResult,
    EnumerationCursor,
    SearchError,
    SearchErrorCode,
    SearchScope,
    SearchState,
)


@dataclass
class StorageCase:
    sessions: async_sessionmaker[AsyncSession]
    scope: SearchScope
    collection_id: uuid.UUID
    column_id: uuid.UUID
    source_id: uuid.UUID

    def store(self, session: AsyncSession) -> SearchStorage:
        return SearchStorage(session, self.scope)


@pytest.fixture
async def storage_case() -> AsyncIterator[StorageCase]:
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    scope = SearchScope(uuid.uuid4(), uuid.uuid4())
    column_id, source_id = uuid.uuid4(), uuid.uuid4()
    async with sessions.begin() as session:
        session.add(
            Organization(
                id=scope.organization_id,
                name="Synthetic search",
                slug=str(scope.organization_id),
            )
        )
        await session.flush()
        session.add(
            Workspace(
                id=scope.workspace_id,
                organization_id=scope.organization_id,
                name="Synthetic workspace",
            )
        )
        await session.flush()
        session.add(
            Table(
                id=source_id, workspace_id=scope.workspace_id, name="synthetic_search"
            )
        )
        await session.flush()
        store = SearchStorage(session, scope)
        await store.save_configuration(
            provider="synthetic",
            model="synthetic-v1",
            endpoint=None,
            credential_id=uuid.uuid4(),
            credential_environment="default",
            dimensions=3,
            input_token_limit=1024,
        )
        collection = await store.configure_collection(
            source_id=source_id,
            column_ids=(column_id,),
            chunker=ChunkerSettings(tokenizer="synthetic"),
            expected_generation=None,
        )
        await store.set_state(SearchState.ACTIVE)
        collection_id = collection.id
    yield StorageCase(sessions, scope, collection_id, column_id, source_id)
    async with sessions.begin() as session:
        for model in (
            SearchChunk,
            SearchDocument,
            SearchCollection,
            SearchEmbeddingConfig,
            SearchWorkspaceState,
        ):
            await session.execute(
                delete(model).where(model.workspace_id == scope.workspace_id)
            )
        await session.execute(
            delete(Workspace).where(Workspace.id == scope.workspace_id)
        )
        await session.execute(
            delete(Organization).where(Organization.id == scope.organization_id)
        )
    await engine.dispose()


async def claimed(case: StorageCase) -> BuildClaim:
    async with case.sessions.begin() as session:
        store = case.store(session)
        document = await store.touch_document(case.collection_id, uuid.uuid4())
        claim = await store.claim(case.collection_id, document.id)
        assert claim is not None
        return claim


def manifest(case: StorageCase, ordinal: int = 0) -> ChunkManifest:
    return ChunkManifest(
        ordinal=ordinal,
        column_id=case.column_id,
        column_name="body",
        start=ordinal * 10,
        end=ordinal * 10 + 10,
        input_hash=f"{ordinal:064x}",
    )


async def prepared(case: StorageCase, count: int = 2) -> BuildClaim:
    claim = await claimed(case)
    async with case.sessions.begin() as session:
        await case.store(session).checkpoint(
            claim,
            before=EnumerationCursor(),
            after=EnumerationCursor(character_offset=count * 10, next_ordinal=count),
            chunks=tuple(manifest(case, ordinal) for ordinal in range(count)),
            complete=True,
        )
    return claim


def embedding(
    claim: BuildClaim, ordinal: int = 0, vector: tuple[float, ...] = (1, 0, 0)
) -> EmbeddingResult:
    return EmbeddingResult(ordinal, f"{ordinal:064x}", claim.config_version, vector)


@pytest.mark.anyio
async def test_publication_requires_all_chunks_and_deduplicates_retries(
    storage_case: StorageCase,
) -> None:
    case = storage_case
    claim = await prepared(case)
    async with case.sessions.begin() as session:
        await case.store(session).write_embeddings(claim, (embedding(claim),))
    with pytest.raises(SearchError) as error:
        async with case.sessions.begin() as session:
            await case.store(session).publish(claim)
    assert error.value.code == SearchErrorCode.INDEX_NOT_READY
    async with case.sessions.begin() as session:
        assert not (await session.scalars(eligible_chunks(case.scope))).all()
        store = case.store(session)
        await store.write_embeddings(claim, (embedding(claim), embedding(claim, 1)))
        await store.publish(claim)
    async with case.sessions.begin() as session:
        assert len((await session.scalars(eligible_chunks(case.scope))).all()) == 2
        document = await session.get(SearchDocument, claim.document_id)
        assert document is not None
        await case.store(session).touch_document(
            case.collection_id, document.source_row_id
        )
        assert not (await session.scalars(eligible_chunks(case.scope))).all()
    with pytest.raises(SearchError):
        async with case.sessions.begin() as session:
            await case.store(session).write_embeddings(claim, (embedding(claim),))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "vector",
    [
        (1.0, 0.0),
        (0.0, 0.0, 0.0),
        (float("nan"), 0.0, 1.0),
        (float("inf"), 0.0, 1.0),
        (1e300, 0.0, 0.0),
        (1e-300, 0.0, 0.0),
    ],
)
async def test_invalid_vectors_rejected(
    storage_case: StorageCase, vector: tuple[float, ...]
) -> None:
    case = storage_case
    claim = await prepared(case, 1)
    with pytest.raises(SearchError) as error:
        async with case.sessions.begin() as session:
            await case.store(session).write_embeddings(
                claim, (embedding(claim, vector=vector),)
            )
    assert error.value.code == SearchErrorCode.INVALID_VECTOR


@pytest.mark.anyio
async def test_two_claimers_and_expired_fence(storage_case: StorageCase) -> None:
    case = storage_case
    async with case.sessions.begin() as session:
        document = await case.store(session).touch_document(
            case.collection_id, uuid.uuid4()
        )
        document_id = document.id

    async def compete() -> BuildClaim | None:
        async with case.sessions.begin() as session:
            return await case.store(session).claim(case.collection_id, document_id)

    claims = await asyncio.gather(compete(), compete())
    assert sum(claim is not None for claim in claims) == 1
    old = next(claim for claim in claims if claim is not None)
    async with case.sessions.begin() as session:
        await session.execute(
            update(SearchDocument)
            .where(SearchDocument.id == document_id)
            .values(lease_until=func.now() - text("INTERVAL '1 second'"))
        )
    new = await compete()
    assert new is not None and new.fence > old.fence
    with pytest.raises(SearchError) as error:
        async with case.sessions.begin() as session:
            await case.store(session).checkpoint(
                old,
                before=EnumerationCursor(),
                after=EnumerationCursor(),
                chunks=(),
                complete=True,
            )
    assert error.value.code == SearchErrorCode.STALE_CLAIM


@pytest.mark.anyio
async def test_empty_document_publication(storage_case: StorageCase) -> None:
    case = storage_case
    claim = await prepared(case, 0)
    async with case.sessions.begin() as session:
        await case.store(session).publish(claim)
        document = await session.get(SearchDocument, claim.document_id)
        assert document is not None and document.state == "empty"
        assert not (await session.scalars(eligible_chunks(case.scope))).all()


@pytest.mark.anyio
async def test_configuration_change_and_pause_reject_workers(
    storage_case: StorageCase,
) -> None:
    case = storage_case
    claim = await prepared(case, 1)
    async with case.sessions.begin() as session:
        await case.store(session).set_state(SearchState.PAUSED)
    with pytest.raises(SearchError):
        async with case.sessions.begin() as session:
            await case.store(session).write_embeddings(claim, (embedding(claim),))
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.set_state(SearchState.ACTIVE)
        await store.save_configuration(
            provider="synthetic",
            model="synthetic-v2",
            endpoint=None,
            credential_id=uuid.uuid4(),
            credential_environment="default",
            dimensions=2,
            input_token_limit=1024,
        )
    with pytest.raises(SearchError):
        async with case.sessions.begin() as session:
            await case.store(session).write_embeddings(claim, (embedding(claim),))


@pytest.mark.anyio
async def test_tombstone_and_cleanup_are_bounded(storage_case: StorageCase) -> None:
    case = storage_case
    claim = await prepared(case)
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.write_embeddings(claim, (embedding(claim), embedding(claim, 1)))
        await store.publish(claim)
        await store.tombstone_collection(case.collection_id)
        assert not (await session.scalars(eligible_chunks(case.scope))).all()
        assert await store.cleanup_chunks(case.collection_id, limit=1) == 1
        assert await store.cleanup_chunks(case.collection_id, limit=1) == 1
        assert await store.cleanup_chunks(case.collection_id, limit=1) == 0


@pytest.mark.anyio
async def test_cross_tenant_and_dimension_constraints(
    storage_case: StorageCase,
) -> None:
    case = storage_case
    claim = await prepared(case, 1)
    with pytest.raises(SearchError):
        async with case.sessions.begin() as session:
            await SearchStorage(
                session, SearchScope(uuid.uuid4(), case.scope.workspace_id)
            ).collection(case.collection_id)
    with pytest.raises(IntegrityError):
        async with case.sessions.begin() as session:
            await session.execute(
                update(SearchChunk)
                .where(SearchChunk.document_id == claim.document_id)
                .values(dimensions=2)
            )
    with pytest.raises(IntegrityError):
        async with case.sessions.begin() as session:
            await session.execute(
                update(SearchChunk)
                .where(SearchChunk.document_id == claim.document_id)
                .values(workspace_id=uuid.uuid4())
            )


@pytest.mark.anyio
async def test_concurrent_enable_uses_absent_collection_lock(
    storage_case: StorageCase,
) -> None:
    case = storage_case
    source_id = uuid.uuid4()

    async def enable() -> bool:
        try:
            async with case.sessions.begin() as session:
                await case.store(session).configure_collection(
                    source_id=source_id,
                    column_ids=(case.column_id,),
                    chunker=ChunkerSettings(tokenizer="synthetic"),
                    expected_generation=None,
                )
            return True
        except SearchError:
            return False

    assert sorted(await asyncio.gather(enable(), enable())) == [False, True]


@pytest.mark.anyio
async def test_checkpoint_and_publication_retries(storage_case: StorageCase) -> None:
    case = storage_case
    claim = await claimed(case)
    async with case.sessions.begin() as session:
        store = case.store(session)
        before = EnumerationCursor()
        after = EnumerationCursor(character_offset=10, next_ordinal=1)
        chunks = (manifest(case),)
        await store.checkpoint(
            claim, before=before, after=after, chunks=chunks, complete=True
        )
        await store.checkpoint(
            claim, before=before, after=after, chunks=chunks, complete=True
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SearchChunk)
                .where(SearchChunk.document_id == claim.document_id)
            )
            == 1
        )
        await store.write_embeddings(claim, (embedding(claim),))
        await store.publish(claim)
        await store.publish(claim)


@pytest.mark.anyio
async def test_paused_edits_and_rollback_cannot_restore_old_results(
    storage_case: StorageCase,
) -> None:
    case = storage_case
    claim = await prepared(case, 1)
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.write_embeddings(claim, (embedding(claim),))
        await store.publish(claim)
        await store.set_state(SearchState.PAUSED)
        document = await session.get(SearchDocument, claim.document_id)
        assert document is not None
        await store.touch_document(case.collection_id, document.source_row_id)
        await store.set_state(SearchState.ACTIVE)
        assert not (await session.scalars(eligible_chunks(case.scope))).all()
        await store.set_state(SearchState.REINDEX_REQUIRED)
        await store.set_state(SearchState.DISABLED)
        with pytest.raises(SearchError) as activation_error:
            await store.set_state(SearchState.ACTIVE)
        assert activation_error.value.code == SearchErrorCode.INDEX_NOT_READY
        assert not (await session.scalars(eligible_chunks(case.scope))).all()
        with pytest.raises(SearchError):
            await store.claim(case.collection_id, claim.document_id)


@pytest.mark.anyio
async def test_workspace_deletion_does_not_cascade_and_cleanup_is_bounded(
    storage_case: StorageCase,
) -> None:
    case = storage_case
    claim = await prepared(case)
    async with case.sessions.begin() as session:
        await session.execute(
            delete(Workspace).where(Workspace.id == case.scope.workspace_id)
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SearchChunk)
                .where(SearchChunk.document_id == claim.document_id)
            )
            == 2
        )
        assert not (await session.scalars(eligible_chunks(case.scope))).all()
        await cleanup_orphans(session, limit=1)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SearchChunk)
                .where(SearchChunk.document_id == claim.document_id)
            )
            == 1
        )
        await cleanup_orphans(session, limit=1)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SearchChunk)
                .where(SearchChunk.document_id == claim.document_id)
            )
            == 0
        )


@pytest.mark.anyio
async def test_database_rls_enforces_both_tenant_ids(storage_case: StorageCase) -> None:
    case = storage_case
    role = f"synthetic_search_{uuid.uuid4().hex}"
    async with case.sessions.begin() as session:
        await session.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
        await session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        await session.execute(
            text(f'GRANT SELECT ON workspace, search_collection TO "{role}"')
        )
        for statement in enable_search_table_rls("search_collection").split(";"):
            if statement.strip():
                await session.execute(text(statement))
        await session.execute(text(f'SET LOCAL ROLE "{role}"'))
        assert not (await session.scalars(select(SearchCollection))).all()
        await session.execute(
            text(
                "SELECT set_config('app.current_workspace_id', :workspace, true), set_config('app.current_org_id', :org, true)"
            ),
            {
                "workspace": str(case.scope.workspace_id),
                "org": str(case.scope.organization_id),
            },
        )
        assert len((await session.scalars(select(SearchCollection))).all()) == 1
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(uuid.uuid4())},
        )
        assert not (await session.scalars(select(SearchCollection))).all()
        await session.execute(text("RESET ROLE"))
        await session.execute(
            text("DROP POLICY rls_policy_search_collection ON search_collection")
        )
        await session.execute(
            text("ALTER TABLE search_collection DISABLE ROW LEVEL SECURITY")
        )
        await session.execute(text(f'DROP OWNED BY "{role}"'))
        await session.execute(text(f'DROP ROLE "{role}"'))


@pytest.mark.anyio
@pytest.mark.parametrize("dimensions", [2, 3072])
async def test_unconstrained_vectors_follow_current_config(
    storage_case: StorageCase, dimensions: int
) -> None:
    case = storage_case
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.save_configuration(
            provider="synthetic",
            model="synthetic-other",
            endpoint=None,
            credential_id=uuid.uuid4(),
            credential_environment="default",
            dimensions=dimensions,
            input_token_limit=1024,
        )
        await store.configure_collection(
            source_id=case.source_id,
            column_ids=(case.column_id,),
            chunker=ChunkerSettings(tokenizer="synthetic"),
            expected_generation=1,
        )
    claim = await prepared(case, 1)
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.write_embeddings(
            claim, (embedding(claim, vector=(1.0,) + (0.0,) * (dimensions - 1)),)
        )
        await store.publish(claim)
        assert len((await session.scalars(eligible_chunks(case.scope))).all()) == 1
        await store.checkpoint_backfill(
            case.collection_id, generation=2, before=None, after=None, complete=True
        )
        status = await store.status(case.collection_id)
        assert status.ready == 1 and not status.partial


@pytest.mark.anyio
@pytest.mark.parametrize("invalidation", ["edit", "delete", "configuration"])
async def test_publication_racing_invalidation_never_leaves_eligible_chunks(
    storage_case: StorageCase, invalidation: str
) -> None:
    case = storage_case
    claim = await prepared(case, 1)
    async with case.sessions.begin() as session:
        await case.store(session).write_embeddings(claim, (embedding(claim),))
        document = await session.get(SearchDocument, claim.document_id)
        assert document is not None
        row_id = document.source_row_id

    async def publish() -> None:
        try:
            async with case.sessions.begin() as session:
                await case.store(session).publish(claim)
        except SearchError:
            pass  # Invalidation won the race.

    async def invalidate() -> None:
        async with case.sessions.begin() as session:
            store = case.store(session)
            if invalidation == "configuration":
                await store.save_configuration(
                    provider="synthetic",
                    model="synthetic-v2",
                    endpoint=None,
                    credential_id=uuid.uuid4(),
                    credential_environment="default",
                    dimensions=3,
                    input_token_limit=1024,
                )
            else:
                await store.touch_document(
                    case.collection_id, row_id, deleted=invalidation == "delete"
                )

    await asyncio.gather(publish(), invalidate())
    async with case.sessions.begin() as session:
        assert not (await session.scalars(eligible_chunks(case.scope))).all()


@pytest.mark.anyio
async def test_enumeration_must_finish_even_when_all_known_chunks_are_embedded(
    storage_case: StorageCase,
) -> None:
    case = storage_case
    claim = await claimed(case)
    after = EnumerationCursor(character_offset=10, next_ordinal=1)
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.checkpoint(
            claim, before=EnumerationCursor(), after=after, chunks=(manifest(case),)
        )
        await store.write_embeddings(claim, (embedding(claim),))
    with pytest.raises(SearchError):
        async with case.sessions.begin() as session:
            await case.store(session).publish(claim)
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.checkpoint(
            claim, before=after, after=after, chunks=(), complete=True
        )
        await store.publish(claim)


@pytest.mark.anyio
async def test_scoped_session_factory_preserves_caller_transaction(
    storage_case: StorageCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = storage_case
    async with case.sessions.begin() as session:
        async with SearchStorage.with_session(
            scope=case.scope, session=session
        ) as store:
            assert store.session is session
            await store.set_state(SearchState.PAUSED)
        assert session.in_transaction()
        # The helper must not close or roll back the caller's transaction.
        assert (
            await session.scalar(
                select(SearchWorkspaceState.state).where(
                    SearchWorkspaceState.workspace_id == case.scope.workspace_id
                )
            )
            == SearchState.PAUSED
        )
    # Route the standard session factory to this test's isolated database.
    monkeypatch.setattr(config, "TRACECAT__DB_URI", TEST_DB_CONFIG.test_url_sync)
    reset_async_engine()
    try:
        async with SearchStorage.with_session(scope=case.scope) as store:
            assert (await store.status(case.collection_id)).state == SearchState.PAUSED
            await store.set_state(SearchState.ACTIVE)
            # No commit: the owned session must roll this change back on exit.
        async with SearchStorage.with_session(scope=case.scope) as store:
            assert (await store.status(case.collection_id)).state == SearchState.PAUSED
    finally:
        await get_async_engine().dispose()
        reset_async_engine()
    with pytest.raises(ValueError, match="explicit tenant scope"):
        async with SearchStorage.with_session():
            pytest.fail("An unscoped storage session must never be yielded")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "intermediate_state", [None, SearchState.DISABLED, SearchState.PAUSED]
)
async def test_reindex_recovery_requires_new_configuration(
    storage_case: StorageCase,
    intermediate_state: SearchState | None,
) -> None:
    case = storage_case
    old_claim = await prepared(case, 1)
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.write_embeddings(old_claim, (embedding(old_claim),))
        await store.publish(old_claim)
        await store.set_state(SearchState.REINDEX_REQUIRED)
        if intermediate_state is not None:
            await store.set_state(intermediate_state)
    with pytest.raises(SearchError) as activation_error:
        async with case.sessions.begin() as session:
            await case.store(session).set_state(SearchState.ACTIVE)
    assert activation_error.value.code == SearchErrorCode.INDEX_NOT_READY
    with pytest.raises(SearchError) as configuration_error:
        async with case.sessions.begin() as session:
            await case.store(session).configure_collection(
                source_id=case.source_id,
                column_ids=(case.column_id,),
                chunker=ChunkerSettings(tokenizer="synthetic"),
                expected_generation=1,
            )
    assert configuration_error.value.code == SearchErrorCode.CONFIGURATION_CHANGED
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.save_configuration(
            provider="synthetic",
            model="synthetic-v2",
            endpoint=None,
            credential_id=uuid.uuid4(),
            credential_environment="default",
            dimensions=3,
            input_token_limit=1024,
        )
        await store.set_state(SearchState.ACTIVE)
        assert not (await session.scalars(eligible_chunks(case.scope))).all()
    with pytest.raises(SearchError):
        async with case.sessions.begin() as session:
            await case.store(session).publish(old_claim)
    async with case.sessions.begin() as session:
        store = case.store(session)
        collection = await store.configure_collection(
            source_id=case.source_id,
            column_ids=(case.column_id,),
            chunker=ChunkerSettings(tokenizer="synthetic"),
            expected_generation=1,
        )
        claim = await store.claim(collection.id, old_claim.document_id)
        assert claim is not None
        assert claim.config_version > old_claim.config_version
        await store.checkpoint(
            claim,
            before=EnumerationCursor(),
            after=EnumerationCursor(character_offset=10, next_ordinal=1),
            chunks=(manifest(case),),
            complete=True,
        )
        await store.write_embeddings(claim, (embedding(claim),))
        await store.publish(claim)
        assert len((await session.scalars(eligible_chunks(case.scope))).all()) == 1


@pytest.mark.anyio
async def test_empty_index_is_partial_after_configuration_change(
    storage_case: StorageCase,
) -> None:
    case = storage_case
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.checkpoint_backfill(
            case.collection_id, generation=1, before=None, after=None, complete=True
        )
        assert not (await store.status(case.collection_id)).partial
        await store.save_configuration(
            provider="synthetic",
            model="synthetic-v2",
            endpoint=None,
            credential_id=uuid.uuid4(),
            credential_environment="default",
            dimensions=3,
            input_token_limit=1024,
        )
        status = await store.status(case.collection_id)
        assert status.partial
        assert status.pending == status.ready == status.empty == status.failed == 0


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["configuration", "generation"])
async def test_stale_failures_become_pending(
    storage_case: StorageCase, change: str
) -> None:
    case = storage_case
    claim = await claimed(case)
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.fail(claim, SearchErrorCode.PROVIDER_UNAVAILABLE)
        status = await store.status(case.collection_id)
        assert status.failed == 1 and status.pending == 0
        if change == "configuration":
            await store.save_configuration(
                provider="synthetic",
                model="synthetic-v2",
                endpoint=None,
                credential_id=uuid.uuid4(),
                credential_environment="default",
                dimensions=3,
                input_token_limit=1024,
            )
        else:
            await store.configure_collection(
                source_id=case.source_id,
                column_ids=(case.column_id,),
                chunker=ChunkerSettings(tokenizer="synthetic", input_tokens=400),
                expected_generation=1,
            )
        status = await store.status(case.collection_id)
        assert status.failed == 0 and status.pending == 1
        assert status.partial


@pytest.mark.anyio
async def test_fresh_workspace_cannot_activate_without_configuration(
    storage_case: StorageCase,
) -> None:
    case = storage_case
    async with case.sessions.begin() as session:
        workspace_id = uuid.uuid4()
        session.add(
            Workspace(
                id=workspace_id,
                organization_id=case.scope.organization_id,
                name="Synthetic unconfigured workspace",
            )
        )
        await session.flush()
        store = SearchStorage(
            session, SearchScope(case.scope.organization_id, workspace_id)
        )
        with pytest.raises(SearchError) as error:
            await store.set_state(SearchState.ACTIVE)
        assert error.value.code == SearchErrorCode.INDEX_NOT_READY
        state = await session.scalar(
            select(SearchWorkspaceState).where(
                SearchWorkspaceState.workspace_id == workspace_id
            )
        )
        assert state is not None
        assert state.state == SearchState.DISABLED and state.current_version == 0
        await session.rollback()
