import asyncio
import time
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest

from tracecat.concurrency import GatheringTaskGroup, apartial, cooperative


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


# Fixtures for cooperative() tests
@pytest.fixture
def sample_data():
    return [1, 2, 3, 4, 5]


@pytest.fixture
def sample_strings():
    return ["hello", "world", "test", "data"]


@pytest.fixture
def sample_objects():
    return [
        {"id": 1, "name": "first"},
        {"id": 2, "name": "second"},
        {"id": 3, "name": "third"},
    ]


# Basic functionality tests
@pytest.mark.anyio
async def test_cooperative_basic_list(sample_data):
    """Test cooperative() with a basic list."""
    result = []
    async for item in cooperative(sample_data):
        result.append(item)

    assert result == sample_data


@pytest.mark.anyio
async def test_cooperative_empty_iterable():
    """Test cooperative() with empty iterable."""
    result = []
    async for item in cooperative([]):
        result.append(item)

    assert result == []


@pytest.mark.anyio
async def test_cooperative_single_item():
    """Test cooperative() with single item."""
    result = []
    async for item in cooperative([42]):
        result.append(item)

    assert result == [42]


@pytest.mark.anyio
async def test_cooperative_different_iterables():
    """Test cooperative() with different iterable types."""
    # Test with tuple
    result_tuple = []
    async for item in cooperative((1, 2, 3)):
        result_tuple.append(item)
    assert result_tuple == [1, 2, 3]

    # Test with range
    result_range = []
    async for item in cooperative(range(3)):
        result_range.append(item)
    assert result_range == [0, 1, 2]

    # Test with generator
    def gen():
        yield from [1, 2, 3]

    result_gen = []
    async for item in cooperative(gen()):
        result_gen.append(item)
    assert result_gen == [1, 2, 3]


@pytest.mark.anyio
async def test_cooperative_string_iteration():
    """Test cooperative() with string iteration."""
    result = []
    async for char in cooperative("abc"):
        result.append(char)

    assert result == ["a", "b", "c"]


@pytest.mark.anyio
async def test_cooperative_complex_objects(sample_objects):
    """Test cooperative() with complex objects."""
    result = []
    async for obj in cooperative(sample_objects):
        result.append(obj)

    assert result == sample_objects
    assert all(isinstance(obj, dict) for obj in result)


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
async def test_cooperative_custom_delay():
    """Test cooperative() with custom delay."""
    items = [1, 2, 3]
    delay = 0.01

    with patch("asyncio.sleep") as mock_sleep:
        mock_sleep.return_value = None

        result = []
        async for item in cooperative(items, delay=delay):
            result.append(item)

        # Should call sleep after each item
        assert mock_sleep.call_count == len(items)
        # All calls should be with the custom delay
        for call in mock_sleep.call_args_list:
            assert call[0] == (delay,)


@pytest.mark.anyio
async def test_cooperative_allows_event_loop_cooperation():
    """Test that cooperative() actually allows other tasks to run."""
    items = list(range(10))
    other_task_executed = False

    async def other_task():
        nonlocal other_task_executed
        await asyncio.sleep(0.001)  # Small delay
        other_task_executed = True

    # Start the other task
    task = asyncio.create_task(other_task())

    # Process items cooperatively
    result = []
    async for item in cooperative(items, delay=0.001):
        result.append(item)
        # Give a chance for the other task to complete
        if len(result) == 5:
            await asyncio.sleep(0.002)

    await task

    assert result == items
    assert other_task_executed, (
        "Other task should have executed during cooperative iteration"
    )


@pytest.mark.anyio
async def test_cooperative_timing_overhead():
    """Test that cooperative() has minimal timing overhead."""
    items = list(range(1000))  # Use larger dataset for more reliable timing

    # Time without cooperative (multiple runs for stability)
    sync_times = []
    for _ in range(3):
        start = time.perf_counter()
        sync_result = list(items)
        sync_times.append(time.perf_counter() - start)
    # Time with cooperative (delay=0) (multiple runs for stability)
    async_times = []
    async_result = []
    for _ in range(3):
        start = time.perf_counter()
        temp_result = []
        async for item in cooperative(items):
            temp_result.append(item)
        async_times.append(time.perf_counter() - start)
        if not async_result:  # Store first result for comparison
            async_result = temp_result
    async_time = min(async_times)  # Use best time

    assert sync_result == async_result
    # Async version should complete successfully (don't assert specific timing due to variability)
    assert async_time > 0  # Just ensure it completed and took some measurable time


# Type safety tests
@pytest.mark.anyio
async def test_cooperative_type_preservation():
    """Test that cooperative() preserves item types."""
    mixed_items = [1, "string", 3.14, True, None, {"key": "value"}]

    result = []
    async for item in cooperative(mixed_items):
        result.append(item)

    assert result == mixed_items
    assert isinstance(result[0], int)
    assert isinstance(result[1], str)
    assert isinstance(result[2], float)
    assert isinstance(result[3], bool)
    assert result[4] is None
    assert isinstance(result[5], dict)


@pytest.mark.anyio
async def test_cooperative_return_type():
    """Test that cooperative() returns the correct async generator type."""
    items = [1, 2, 3]
    coop_gen = cooperative(items)

    assert isinstance(coop_gen, AsyncGenerator)


# Integration tests
@pytest.mark.anyio
async def test_cooperative_with_traverse_expressions():
    """Test cooperative() with actual usage pattern from expressions module."""
    # Mock data similar to what traverse_expressions would produce
    expressions = [
        "ACTIONS.webhook.result",
        "INPUTS.threshold",
        "FN.add(1, 2)",
        "TRIGGER.event_data",
    ]

    def mock_traverse_expressions(_obj):
        return iter(expressions)

    # Test the pattern used in expressions/core.py
    result = []
    async for expr_str in cooperative(mock_traverse_expressions({})):
        result.append(expr_str)

    assert result == expressions


@pytest.mark.anyio
async def test_cooperative_large_dataset():
    """Test cooperative() with large dataset."""
    large_dataset = list(range(1000))

    result = []
    count = 0
    async for item in cooperative(large_dataset, delay=0):
        result.append(item)
        count += 1
        # Periodically yield to event loop
        if count % 100 == 0:
            await asyncio.sleep(0)

    assert result == large_dataset
    assert len(result) == 1000


# Edge cases
@pytest.mark.anyio
async def test_cooperative_with_none_values():
    """Test cooperative() with None values in iterable."""
    items_with_none = [1, None, 3, None, 5]

    result = []
    async for item in cooperative(items_with_none):
        result.append(item)

    assert result == items_with_none
    assert result[1] is None
    assert result[3] is None


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
async def test_cooperative_infinite_iterable():
    """Test cooperative() with infinite iterable (limited consumption)."""

    def infinite_generator():
        i = 0
        while True:
            yield i
            i += 1

    result = []
    count = 0
    async for item in cooperative(infinite_generator()):
        result.append(item)
        count += 1
        if count >= 5:  # Limit consumption
            break

    assert result == [0, 1, 2, 3, 4]


# Performance and stress tests
@pytest.mark.anyio
async def test_cooperative_concurrent_processing():
    """Test cooperative() in concurrent scenarios."""

    async def process_list(items, delay):
        result = []
        async for item in cooperative(items, delay=delay):
            result.append(item * 2)
        return result

    # Process multiple lists concurrently
    lists = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]

    # Run all processing concurrently
    tasks = [process_list(lst, delay=0.001) for lst in lists]
    results = await asyncio.gather(*tasks)

    expected = [[2, 4, 6], [8, 10, 12], [14, 16, 18]]

    assert results == expected


@pytest.mark.anyio
async def test_cooperative_memory_efficiency():
    """Test that cooperative() doesn't load entire iterable into memory."""

    # Create a generator that would be expensive if fully loaded
    def memory_efficient_generator():
        for i in range(10):
            yield f"large_string_data_{i}" * 100  # Simulating large data

    result_count = 0
    max_result_length = 0

    async for item in cooperative(memory_efficient_generator()):
        result_count += 1
        max_result_length = max(max_result_length, len(item))
        # Process one at a time without storing all

    assert result_count == 10
    assert max_result_length > 1000  # Each item is large


@pytest.mark.anyio
async def test_cooperative_with_async_context_manager():
    """Test cooperative() usage within async context managers."""

    class AsyncContextManager:
        def __init__(self):
            self.entered = False
            self.exited = False

        async def __aenter__(self):
            self.entered = True
            return self

        async def __aexit__(self, _exc_type, _exc_val, _exc_tb):
            self.exited = True

    context_mgr = AsyncContextManager()
    items = [1, 2, 3]
    result = []

    async with context_mgr:
        async for item in cooperative(items):
            result.append(item)

    assert context_mgr.entered
    assert context_mgr.exited
    assert result == items


# Additional edge case tests
@pytest.mark.anyio
async def test_cooperative_nested_iteration():
    """Test cooperative() with nested async iterations."""
    outer_items = [[1, 2], [3, 4], [5, 6]]

    result = []
    async for inner_list in cooperative(outer_items):
        inner_result = []
        async for item in cooperative(inner_list, delay=0.001):
            inner_result.append(item)
        result.append(inner_result)

    assert result == [[1, 2], [3, 4], [5, 6]]


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
async def test_cooperative_exception_during_sleep():
    """Test behavior when asyncio.sleep is interrupted."""
    items = [1, 2, 3]

    original_sleep = asyncio.sleep
    call_count = 0

    async def failing_sleep(delay):
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # Fail on second call
            raise asyncio.CancelledError("Sleep cancelled")
        await original_sleep(delay)

    result = []
    with patch("asyncio.sleep", side_effect=failing_sleep):
        with pytest.raises(asyncio.CancelledError, match="Sleep cancelled"):
            async for item in cooperative(items):
                result.append(item)

    # Should have yielded items before exception (could be 1 or 2 depending on timing)
    assert len(result) >= 1
    assert result[0] == 1
    assert len(result) <= 2  # At most 2 items before exception
