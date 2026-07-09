from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator
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
    expire_call_count = 0
    set_calls: list[dict[str, object]] = []
    _xadd_raised_once = False
    raise_on_xadd_once = False
    raise_on_sadd = False
    raise_on_set = False
    set_result: object | None = None

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
        DummyRedis.expire_call_count += 1
        return None

    async def set(self, *args, **kwargs) -> object | None:
        DummyRedis.set_calls.append(kwargs)
        if DummyRedis.raise_on_set:
            raise RuntimeError("transport closed")
        return DummyRedis.set_result

    async def sadd(self, *args, **kwargs) -> int:
        if DummyRedis.raise_on_sadd:
            raise RuntimeError("transport closed")
        return 1

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
        cls.expire_call_count = 0
        cls.set_calls.clear()
        cls._xadd_raised_once = False
        cls.raise_on_xadd_once = False
        cls.raise_on_sadd = False
        cls.raise_on_set = False
        cls.set_result = None


def fake_from_url(*args, **kwargs) -> SimpleNamespace:
    pool = DummyConnectionPool()
    return SimpleNamespace(connection_pool=pool)


def reset_singleton() -> None:
    for task in list(RedisClient._reset_tasks):
        task.cancel()
    RedisClient._reset_tasks.clear()
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
    retry_controller = RedisClient.xadd.retry  # type: ignore[attr-defined]
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


def test_xadd_applies_default_ttl() -> None:
    client = RedisClient()

    asyncio.run(client.xadd("stream", {"field": "value"}))

    assert DummyRedis.expire_call_count == 1


def test_xadd_allows_disabling_ttl() -> None:
    client = RedisClient()

    asyncio.run(client.xadd("stream", {"field": "value"}, expire_seconds=None))

    assert DummyRedis.expire_call_count == 0


def test_set_audit_get_uses_set_ex_get_without_retries() -> None:
    client = RedisClient()
    DummyRedis.set_result = "1"
    key = "audit:usage:kind:key:ip"

    result = asyncio.run(client.set_audit_get(key, "1", expire_seconds=900))

    assert result == "1"
    assert DummyRedis.set_calls == [{"name": key, "value": "1", "ex": 900, "get": True}]


@pytest.mark.parametrize(
    ("helper_name", "failure_flag", "attempts"),
    [
        ("xadd_audit", "raise_on_xadd_once", 1),
        ("sadd_audit", "raise_on_sadd", 1),
        ("set_audit_get", "raise_on_set", 2),
    ],
)
def test_audit_helpers_reset_in_background_and_dedupe(
    helper_name: str,
    failure_flag: str,
    attempts: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        client = RedisClient()
        reset_started = asyncio.Event()
        reset_can_finish = asyncio.Event()
        reset_count = 0

        async def slow_reset() -> None:
            nonlocal reset_count
            reset_count += 1
            reset_started.set()
            await reset_can_finish.wait()

        monkeypatch.setattr(client, "_reset_connection", slow_reset)
        calls: dict[str, Callable[[], Awaitable[object]]] = {
            "xadd_audit": lambda: client.xadd_audit("stream", {"field": "value"}),
            "sadd_audit": lambda: client.sadd_audit("set", "value"),
            "set_audit_get": lambda: client.set_audit_get(
                "marker", "1", expire_seconds=900
            ),
        }
        setattr(DummyRedis, failure_flag, True)

        for _ in range(attempts):
            with pytest.raises(RuntimeError, match="transport closed"):
                await asyncio.wait_for(calls[helper_name](), timeout=0.05)

        await asyncio.wait_for(reset_started.wait(), timeout=0.05)
        assert reset_count == 1
        assert len(RedisClient._reset_tasks) == 1
        reset_can_finish.set()
        await asyncio.gather(*list(RedisClient._reset_tasks), return_exceptions=True)

    asyncio.run(run())
