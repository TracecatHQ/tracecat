"""The dispatcher replenishes capacity without overlapping workspace work."""

import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest

from tracecat.search.indexing_types import (
    CollectionWork,
    DispatchPage,
    IndexingProgress,
)
from tracecat.search.indexing_workflow import (
    SearchIndexDispatcher,
    discover_search_collections,
)


@pytest.mark.anyio
async def test_dispatch_replenishes_slots_and_serializes_each_workspace():
    work = [
        CollectionWork(
            organization_id=uuid4(), workspace_id=uuid4(), collection_id=uuid4()
        )
        for _ in range(7)
    ]
    same_workspace = work[0].model_copy(update={"collection_id": uuid4()})
    # Waiting for the same workspace must not consume a global slot.
    page = [work[0], same_workspace, *work[1:]]
    started = {item.collection_id: asyncio.Event() for item in page}
    release = {item.collection_id: asyncio.Event() for item in page}
    active = set()
    maximum = 0

    async def execute(fn, argument, **kwargs):
        nonlocal maximum
        if fn is discover_search_collections:
            return DispatchPage(collections=page)
        assert argument.workspace_id not in active
        active.add(argument.workspace_id)
        maximum = max(maximum, len(active))
        started[argument.collection_id].set()
        try:
            await release[argument.collection_id].wait()
        finally:
            active.remove(argument.workspace_id)
        return IndexingProgress(outcome="idle")

    with patch(
        "tracecat.search.indexing_workflow.workflow.execute_activity", new=execute
    ):
        task = asyncio.create_task(SearchIndexDispatcher().run())
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(started[item.collection_id].wait() for item in work[:6])
                ),
                2,
            )
            assert not started[work[6].collection_id].is_set()
            assert not started[same_workspace.collection_id].is_set()
            release[work[1].collection_id].set()
            await asyncio.wait_for(started[work[6].collection_id].wait(), 2)
            # Five original jobs are still blocked: no full-wave barrier remains.
            assert not release[work[0].collection_id].is_set()
            assert not started[same_workspace.collection_id].is_set()
        finally:
            for event in release.values():
                event.set()
            await asyncio.wait_for(task, 2)
    assert started[same_workspace.collection_id].is_set()
    assert maximum == 6
