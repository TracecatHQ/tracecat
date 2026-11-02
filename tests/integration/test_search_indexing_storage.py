"""Storage owns scheduler eligibility, publication, and bounded telemetry."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update

from tests.integration import test_search_storage as storage
from tracecat.db.models import SearchChunk, SearchDocument
from tracecat.search.types import SearchError, SearchErrorCode

pytestmark = pytest.mark.anyio
storage_case = storage.storage_case


async def test_backlog_sample_is_bounded_and_does_not_hold_the_write_lock(storage_case):
    case = storage_case
    async with case.sessions.begin() as session:
        await case.store(session).touch_documents(
            case.collection_id, [uuid4() for _ in range(150)]
        )
    async with case.sessions.begin() as reader:
        sample = await case.store(reader).sample_backlog(case.collection_id)
        assert sample.pending == 100 and sample.failed == 0
        # Leave the sampling transaction open: ordinary writers must still proceed.
        async with case.sessions.begin() as writer:
            await writer.execute(text("SET LOCAL lock_timeout = '1s'"))
            await asyncio.wait_for(case.store(writer).lock_scope(), 2)


@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize(
    "state,lease,retry,stale,deleted,eligible",
    [
        ("pending", None, None, False, False, True),
        ("building", 60, None, False, False, False),
        ("building", -60, None, False, False, True),
        ("failed", None, None, False, False, False),
        ("failed", None, 60, False, False, False),
        ("failed", None, -60, False, False, True),
        ("ready", None, None, False, False, False),
        ("building", 60, None, True, False, True),
        ("pending", None, None, False, True, False),
    ],
)
async def test_explicit_and_scheduled_claims_share_eligibility(
    storage_case, scheduled, state, lease, retry, stale, deleted, eligible
):
    case = storage_case
    now = datetime.now(UTC)
    async with case.sessions.begin() as session:
        doc = await case.store(session).touch_document(case.collection_id, uuid4())
        doc.state = state
        doc.lease_until = now + timedelta(seconds=lease) if lease else None
        doc.next_attempt_at = now + timedelta(seconds=retry) if retry else None
        doc.deleted_at = now if deleted else None
        if state == "ready":
            doc.indexed_revision = doc.desired_revision
            doc.build_revision = doc.desired_revision
            doc.enumeration_complete = True
        if stale:
            collection = await case.store(session).collection(case.collection_id)
            collection.generation += 1
        identifier = doc.id
    async with case.sessions.begin() as session:
        store = case.store(session)
        if scheduled:
            result = await store.claim_next_due(case.collection_id)
            claim = result.claim if result else None
            if result:
                assert result.queue_wait_seconds >= 0
        else:
            claim = await store.claim(case.collection_id, identifier)
        assert (claim is not None) == eligible
        if claim:
            assert claim.document_id == identifier
            assert await store.claim_next_due(case.collection_id) is None
            assert await store.claim(case.collection_id, identifier) is None


async def test_finishing_yields_partial_work_and_publishes_complete_work(storage_case):
    case = storage_case
    claim = await storage.prepared(case)
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.write_embeddings(claim, (storage.embedding(claim),))
        assert await store.finish_or_yield(claim) == "progress"
        doc = await session.get(SearchDocument, claim.document_id)
        assert doc is not None and doc.indexed_revision is None
        next_work = await store.claim_next_due(case.collection_id)
        assert next_work is not None
        await store.write_embeddings(
            next_work.claim, (storage.embedding(next_work.claim, 1),)
        )
        assert await store.finish_or_yield(next_work.claim) == "published"
        assert doc.indexed_revision == doc.desired_revision
        await store.publish(next_work.claim)  # Publication remains idempotent.


async def test_finishing_rejects_a_noncontiguous_manifest(storage_case):
    case = storage_case
    claim = await storage.prepared(case)
    async with case.sessions.begin() as session:
        store = case.store(session)
        await store.write_embeddings(
            claim, (storage.embedding(claim), storage.embedding(claim, 1))
        )
        await session.execute(
            update(SearchChunk)
            .where(
                SearchChunk.document_id == claim.document_id,
                SearchChunk.ordinal == 1,
            )
            .values(ordinal=3)
        )
    with pytest.raises(SearchError) as error:
        async with case.sessions.begin() as session:
            await case.store(session).finish_or_yield(claim)
    assert error.value.code == SearchErrorCode.INDEX_NOT_READY
    async with case.sessions.begin() as session:
        doc = await session.scalar(
            select(SearchDocument).where(SearchDocument.id == claim.document_id)
        )
        assert doc is not None and doc.indexed_revision is None
