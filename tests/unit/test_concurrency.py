import asyncio

import pytest

from tracecat.concurrency import GatheringTaskGroup


@pytest.mark.asyncio
async def test_gathering_task_group():
    # Create a GatheringTaskGroup instance

    # Create some mock coroutines
    async def mock_coroutine(value):
        await asyncio.sleep(0.1)
        return value

    # Add the coroutines to the task group
    async with GatheringTaskGroup() as group:
        for coro in [mock_coroutine(i) for i in range(5)]:
            group.create_task(coro)

    # Check the results
    assert group.results() == [0, 1, 2, 3, 4]
