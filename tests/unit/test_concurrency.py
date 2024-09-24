import asyncio
import os
import random

import pytest

from tracecat.concurrency import AsyncAwareEnviron, GatheringTaskGroup, apartial
from tracecat.logger import logger


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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_async_aware_environ():
    async def passthrough(value: str):
        logger.debug(f"FOO-{value}: original: {value}")
        os.environ["__FOO"] = value
        logger.debug(f"FOO-{value}: Setting value: {value}")
        await asyncio.sleep(random.random())
        logger.debug(f"FOO-{value}: current 1:", os.environ.get("__FOO"))

        await asyncio.sleep(random.random())
        logger.debug(f"FOO-{value}: current 2:", os.environ.get("__FOO"))
        # Return the final value of __FOO
        return os.environ.get("__FOO")

    # We mainly test with parallelism
    with AsyncAwareEnviron.sandbox():
        logger.debug("INSIDE SANDBOX")
        inside_1 = await asyncio.gather(
            passthrough("1111"), passthrough("2222"), passthrough("333")
        )
        assert inside_1 == ["1111", "2222", "333"]

    logger.debug("OUTSIDE SANDBOX")
    # Since we're not in a sandbox, the values should be the last one set
    # as the coroutines are dispatched in order
    outside_1 = await asyncio.gather(
        passthrough("1111"), passthrough("2222"), passthrough("333")
    )
    assert outside_1 == ["333", "333", "333"]

    with AsyncAwareEnviron.sandbox():
        logger.debug("INSIDE SANDBOX")
        async with GatheringTaskGroup() as tg:
            tg.create_task(passthrough("1111"))
            tg.create_task(passthrough("2222"))
            tg.create_task(passthrough("333"))

        inside_2 = tg.results()
        assert inside_2 == ["1111", "2222", "333"]


def test_async_aware_environ_is_instantiated():
    original_environ = os.environ
    with AsyncAwareEnviron.sandbox():
        assert isinstance(os.environ, AsyncAwareEnviron)
    assert not isinstance(os.environ, AsyncAwareEnviron)
    assert isinstance(os.environ, os._Environ)

    if os.environ != original_environ:
        pytest.fail(
            "os.environ is not equivalent to the original environment."
            "Environment not shown for security"
        )


def test_async_aware_environ_basic():
    with AsyncAwareEnviron.sandbox():
        os.environ["TEST_VAR"] = "test_value"
        assert os.environ["TEST_VAR"] == "test_value"

    assert "TEST_VAR" not in os.environ


@pytest.mark.asyncio
async def test_async_aware_environ_multiple_coroutines():
    async def set_env(key: str, value: str):
        # The environment variable is set in the local scope
        # This is only available in child coroutines
        os.environ[key] = value
        await asyncio.sleep(0.1)
        return os.environ[key]

    with AsyncAwareEnviron.sandbox():
        results = await asyncio.gather(
            set_env("VAR1", "value1"),
            set_env("VAR2", "value2"),
            set_env("VAR3", "value3"),
        )

        assert results == ["value1", "value2", "value3"]
        assert "VAR1" not in os.environ
        assert "VAR2" not in os.environ
        assert "VAR3" not in os.environ

    assert "VAR1" not in os.environ
    assert "VAR2" not in os.environ
    assert "VAR3" not in os.environ


def test_async_aware_environ_methods():
    with AsyncAwareEnviron.sandbox():
        os.environ.update({"KEY1": "value1", "KEY2": "value2"})
        assert os.environ.local == {"KEY1": "value1", "KEY2": "value2"}

        assert os.environ.pop("KEY1") == "value1"
        assert "KEY1" not in os.environ

        assert os.environ.setdefault("KEY3", "value3") == "value3"
        assert os.environ["KEY3"] == "value3"

        os.environ.clear()
        assert len(os.environ.local) == 0


def test_async_aware_environ_exception_handling():
    original_env = os.environ.copy()

    try:
        with AsyncAwareEnviron.sandbox():
            os.environ["TEST_VAR"] = "test_value"
            raise ValueError("Test exception")
    except ValueError:
        pass

    assert os.environ == original_env
    assert "TEST_VAR" not in os.environ
