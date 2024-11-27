import asyncio

import pytest

from tracecat.concurrency import GatheringTaskGroup, apartial


@pytest.mark.anyio
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


@pytest.mark.anyio
async def test_apartial():
    async def mock_coroutine(a, b, c):
        await asyncio.sleep(0.1)
        return a + b + c

    partial_coroutine = apartial(mock_coroutine, 1, 2, c=3)
    result = await partial_coroutine()
    assert result == 6

    partial_coroutine = apartial(mock_coroutine, 1, c=3)
    result = await partial_coroutine(2)
    assert result == 6

    partial_coroutine = apartial(mock_coroutine, 1, 2)
    result = await partial_coroutine(3)
    assert result == 6
