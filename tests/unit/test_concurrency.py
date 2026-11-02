import asyncio
import time
from unittest.mock import patch

import pytest

from tracecat.concurrency import (
    GatheringTaskGroup,
    apartial,
    cooperative,
    cooperative_every,
    drain_future_through_cancellation,
    rejoin_future_through_cancellation,
)


@pytest.mark.anyio
async def test_gathering_task_group():
    # Create a GatheringTaskGroup instance

    # Create some mock coroutines
    async def mock_coroutine(value):
        await asyncio.sleep(0)
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
        await asyncio.sleep(0)
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


# Fixtures for cooperative() tests
@pytest.fixture
def sample_data():
    return [1, 2, 3, 4, 5]


# Basic functionality tests
@pytest.mark.anyio
async def test_cooperative_basic_list(sample_data):
    """Test cooperative() with a basic list."""
    result = []
    async for item in cooperative(sample_data):
        result.append(item)

    assert result == sample_data


# Timing and cooperation tests
@pytest.mark.anyio
async def test_cooperative_default_delay():
    """Test cooperative() with default delay (0)."""
    items = [1, 2, 3]

    with patch("asyncio.sleep") as mock_sleep:
        mock_sleep.return_value = None

        result = []
        async for item in cooperative(items):
            result.append(item)

        # Should call sleep after each item
        assert mock_sleep.call_count == len(items)
        # All calls should be with delay=0
        for call in mock_sleep.call_args_list:
            assert call[0] == (0,)


@pytest.mark.anyio
async def test_cooperative_every_sleep_frequency():
    """Test that cooperative_every() sleeps once per checkpoint."""
    items = [1, 2, 3, 4, 5]

    with patch("asyncio.sleep") as mock_sleep:
        mock_sleep.return_value = None

        result = []
        async for item in cooperative_every(items, every=2):
            result.append(item)

        assert result == items
        assert mock_sleep.call_count == 2
        for call in mock_sleep.call_args_list:
            assert call[0] == (0,)


@pytest.mark.anyio
async def test_cooperative_every_invalid_frequency():
    """Test cooperative_every() validates the checkpoint frequency."""
    with pytest.raises(ValueError, match="'every' must be greater than 0"):
        async for _ in cooperative_every([1, 2, 3], every=0):
            pass


@pytest.mark.anyio
async def test_cooperative_generator_exception():
    """Test cooperative() behavior when source generator raises exception."""

    def failing_generator():
        yield 1
        yield 2
        raise ValueError("Generator error")
        yield 3  # This should not be reached

    result = []
    with pytest.raises(ValueError, match="Generator error"):
        async for item in cooperative(failing_generator()):
            result.append(item)

    # Should have yielded items before the exception
    assert result == [1, 2]


@pytest.mark.anyio
async def test_cooperative_with_custom_delay_validation():
    """Test that custom delays are actually applied."""
    items = [1, 2, 3]
    delay = 0.01

    start_time = time.perf_counter()
    result = []
    async for item in cooperative(items, delay=delay):
        result.append(item)
    total_time = time.perf_counter() - start_time

    # Should take at least the total delay time (allowing for some timing variance)
    expected_min_time = delay * len(items) * 0.5  # 50% tolerance for timing variance
    assert total_time >= expected_min_time
    assert result == items


@pytest.mark.anyio
async def test_drain_future_returns_failure_after_repeated_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    cleanup_error = RuntimeError("cleanup failed")

    async def operation() -> None:
        started.set()
        await release.wait()
        raise cleanup_error

    operation_task = asyncio.create_task(operation())
    draining_task = asyncio.create_task(
        drain_future_through_cancellation(operation_task)
    )
    await started.wait()

    draining_task.cancel()
    await asyncio.sleep(0)
    draining_task.cancel()
    await asyncio.sleep(0)
    assert not draining_task.done()

    release.set()
    assert await draining_task is cleanup_error


@pytest.mark.anyio
async def test_drain_future_ignores_cancellation_racing_success() -> None:
    """A completed future's success wins over racing caller cancellation."""
    fut = asyncio.get_running_loop().create_future()
    draining_task = asyncio.create_task(drain_future_through_cancellation(fut))
    await asyncio.sleep(0)

    fut.set_result(None)
    draining_task.cancel()
    assert await draining_task is None


@pytest.mark.anyio
async def test_drain_future_reports_failure_racing_cancellation() -> None:
    """A completed future's failure wins over racing caller cancellation."""
    fut = asyncio.get_running_loop().create_future()
    draining_task = asyncio.create_task(drain_future_through_cancellation(fut))
    await asyncio.sleep(0)

    error = RuntimeError("cleanup failed")
    fut.set_exception(error)
    draining_task.cancel()
    assert await draining_task is error


@pytest.mark.anyio
async def test_drain_future_reports_cancelled_future() -> None:
    """A future's own cancellation is returned as its terminal outcome."""
    fut = asyncio.get_running_loop().create_future()
    draining_task = asyncio.create_task(drain_future_through_cancellation(fut))
    await asyncio.sleep(0)

    fut.cancel()
    draining_task.cancel()
    result = await draining_task
    assert isinstance(result, asyncio.CancelledError)


@pytest.mark.anyio
async def test_rejoin_future_finishes_through_repeated_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def operation() -> None:
        started.set()
        await release.wait()
        finished.set()

    operation_task = asyncio.create_task(operation())
    joining_task = asyncio.create_task(
        rejoin_future_through_cancellation(operation_task)
    )
    await started.wait()

    joining_task.cancel()
    await asyncio.sleep(0)
    joining_task.cancel()
    await asyncio.sleep(0)
    assert not joining_task.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await joining_task
    assert finished.is_set()


@pytest.mark.anyio
async def test_rejoin_future_chains_cleanup_failure_from_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    cleanup_error = RuntimeError("cleanup failed")

    async def operation() -> None:
        started.set()
        await release.wait()
        raise cleanup_error

    operation_task = asyncio.create_task(operation())
    joining_task = asyncio.create_task(
        rejoin_future_through_cancellation(operation_task)
    )
    await started.wait()

    joining_task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError) as raised:
        await joining_task
    assert raised.value.__cause__ is cleanup_error
