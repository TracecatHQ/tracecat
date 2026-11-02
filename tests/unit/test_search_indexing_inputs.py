"""Fresh preparation keeps CPU work off-loop and persists before provider use."""

import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from tracecat.search import indexing
from tracecat.search.chunking_types import SourceSlice
from tracecat.search.embeddings.types import ModelSpec, PinnedConfiguration
from tracecat.search.indexing_types import (
    CollectionWork,
    IndexingOutcome,
    IndexingProgress,
)
from tracecat.search.types import BuildClaim


@pytest.mark.anyio
async def test_cold_tokenizer_initialization_and_counts_are_off_loop(monkeypatch):
    loop_thread = threading.get_ident()
    loop = asyncio.get_running_loop()
    initialized = asyncio.Event()
    release = threading.Event()
    work = CollectionWork(
        organization_id=uuid4(), workspace_id=uuid4(), collection_id=uuid4()
    )
    column = SimpleNamespace(id=uuid4(), name="body")
    claim = BuildClaim(work.collection_id, uuid4(), 1, 1, 1, 1)
    pinned = PinnedConfiguration(
        1,
        ModelSpec(model="all-minilm:22m", dimensions=384, tokenizer="utf8-bytes:v1"),
        uuid4(),
        "default",
    )

    class Counter:
        identity = "utf8-bytes:v1"

        def count_tokens(self, text: str) -> int:
            assert threading.get_ident() != loop_thread
            return len(text.encode())

    def initialize(spec):
        assert threading.get_ident() != loop_thread
        loop.call_soon_threadsafe(initialized.set)
        assert release.wait(2), "tokenizer initialization blocked the event loop"
        return Counter()

    source = Mock()
    source.scope = work.scope
    source._fenced = AsyncMock(
        return_value=(
            SimpleNamespace(source_id=uuid4(), selected_column_ids=[column.id]),
            SimpleNamespace(enumeration_cursor=None, enumeration_complete=False),
        )
    )
    source.table = AsyncMock(return_value=SimpleNamespace(columns=[column]))
    source._scope.return_value = True
    source.session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    source.session.commit = AsyncMock()
    source.checkpoint = AsyncMock()

    async def read_slice(*args):
        assert threading.get_ident() == loop_thread
        return SourceSlice(text="synthetic text", end_of_column=True)

    source.read_slice = read_slice

    @asynccontextmanager
    async def with_session(**kwargs):
        yield source

    monkeypatch.setattr(indexing.TableSearchSource, "with_session", with_session)
    monkeypatch.setattr(indexing, "token_counter", initialize)
    progress = IndexingProgress(outcome=IndexingOutcome.IDLE)
    task = asyncio.create_task(indexing._prepare_inputs(work, claim, pinned, progress))
    try:
        await asyncio.wait_for(initialized.wait(), 1)
        release.set()
        inputs = await asyncio.wait_for(task, 2)
    finally:
        release.set()
        await task
    assert inputs[0].text == "body:\nsynthetic text"
    assert progress.prepared == 1
    source.checkpoint.assert_awaited_once()
    source.session.commit.assert_awaited_once()
    source.session.scalars.assert_awaited_once()
