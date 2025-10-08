from __future__ import annotations

import asyncio
from collections.abc import Generator
from types import SimpleNamespace

import pytest
import tenacity

from tracecat.redis.client import RedisClient


class DummyConnectionPool:
    instances: list[DummyConnectionPool] = []

    def __init__(self) -> None:
        self.disconnected = False
        DummyConnectionPool.instances.append(self)

    async def disconnect(self) -> None:
        self.disconnected = True

    @classmethod
    def reset(cls) -> None:
        cls.instances.clear()


class DummyRedis:
    instances: list[DummyRedis] = []
    xadd_call_count = 0
    _xadd_raised_once = False
    raise_on_xadd_once = False

    def __init__(self, *, connection_pool: DummyConnectionPool) -> None:
        self.connection_pool = connection_pool
        self.closed = False
        DummyRedis.instances.append(self)

    async def close(self) -> None:
        self.closed = True

    async def ping(self) -> bool:
        return True

    async def xadd(self, *args, **kwargs) -> str:
        DummyRedis.xadd_call_count += 1
        if DummyRedis.raise_on_xadd_once and not DummyRedis._xadd_raised_once:
            DummyRedis._xadd_raised_once = True
            raise RuntimeError("transport closed")
        return "1-0"

    async def expire(self, *args, **kwargs) -> None:
        return None

    async def xread(
        self, *args, **kwargs
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        return []

    async def xrange(self, *args, **kwargs) -> list[tuple[str, dict[str, str]]]:
        return []

    @classmethod
    def reset(cls) -> None:
        cls.instances.clear()
        cls.xadd_call_count = 0
        cls._xadd_raised_once = False
        cls.raise_on_xadd_once = False


def fake_from_url(*args, **kwargs) -> SimpleNamespace:
    pool = DummyConnectionPool()
    return SimpleNamespace(connection_pool=pool)


def reset_singleton() -> None:
    RedisClient._instance = None
    RedisClient._pool = None
    RedisClient._client = None
    RedisClient._loop = None


@pytest.fixture(autouse=True)
def patch_redis(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    DummyRedis.reset()
    DummyConnectionPool.reset()
    reset_singleton()

    monkeypatch.setattr("tracecat.redis.client.redis.Redis", DummyRedis)
    monkeypatch.setattr("tracecat.redis.client.redis.from_url", fake_from_url)

    yield

    reset_singleton()
    DummyRedis.reset()
    DummyConnectionPool.reset()


@pytest.fixture(autouse=True)
def no_retry_wait() -> Generator[None, None, None]:
    retry_controller = RedisClient.xadd.retry
    original_wait = retry_controller.wait
    retry_controller.wait = tenacity.wait.wait_fixed(0)
    try:
        yield
    finally:
        retry_controller.wait = original_wait


async def _two_clients_same_loop(client: RedisClient):
    first = await client._get_client()
    second = await client._get_client()
    return first, second


def test_get_client_reuses_same_event_loop() -> None:
    client = RedisClient()
    first, second = asyncio.run(_two_clients_same_loop(client))

    assert first is second
    assert len(DummyRedis.instances) == 1
    assert DummyConnectionPool.instances[0].disconnected is False


def test_get_client_reinitializes_after_loop_change() -> None:
    client = RedisClient()

    first_client = asyncio.run(client._get_client())
    assert len(DummyRedis.instances) == 1

    second_client = asyncio.run(client._get_client())

    assert second_client is not first_client
    assert len(DummyRedis.instances) == 2
    assert DummyRedis.instances[0].closed is True
    assert DummyConnectionPool.instances[0].disconnected is True


def test_xadd_retries_after_transport_error() -> None:
    client = RedisClient()
    DummyRedis.raise_on_xadd_once = True

    result = asyncio.run(client.xadd("stream", {"field": "value"}))

    assert result == "1-0"
    assert DummyRedis.xadd_call_count == 2
    # Pool is reinitialized after the reset
    assert len(DummyConnectionPool.instances) >= 2
    assert DummyRedis.instances[0].closed is True
