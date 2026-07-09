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


class DummyPipeline:
    instances: list[DummyPipeline] = []

    def __init__(self, *, transaction: bool) -> None:
        self.transaction = transaction
        self.commands: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.execute_count = 0
        DummyPipeline.instances.append(self)

    def xadd(self, *args: object, **kwargs: object) -> DummyPipeline:
        self.commands.append(("xadd", args, kwargs))
        return self

    def expire(self, *args: object, **kwargs: object) -> DummyPipeline:
        self.commands.append(("expire", args, kwargs))
        return self

    def sadd(self, *args: object, **kwargs: object) -> DummyPipeline:
        self.commands.append(("sadd", args, kwargs))
        return self

    async def execute(self) -> list[object]:
        self.execute_count += 1
        return ["1-0", True, 1]

    @classmethod
    def reset(cls) -> None:
        cls.instances.clear()


class DummyRedis:
    instances: list[DummyRedis] = []
    xadd_call_count = 0
    expire_call_count = 0
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
        DummyRedis.expire_call_count += 1
        return None

    def pipeline(self, *, transaction: bool = True) -> DummyPipeline:
        return DummyPipeline(transaction=transaction)

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
    DummyPipeline.reset()
    DummyConnectionPool.reset()
    reset_singleton()

    monkeypatch.setattr("tracecat.redis.client.redis.Redis", DummyRedis)
    monkeypatch.setattr("tracecat.redis.client.redis.from_url", fake_from_url)

    yield

    reset_singleton()
    DummyRedis.reset()
    DummyPipeline.reset()
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


def test_publish_audit_atomically_enqueues_and_registers_stream() -> None:
    client = RedisClient()

    result = asyncio.run(
        client.publish_audit(
            "audit:delivery:organization:org-id",
            {"event": "payload"},
            discovery_key="audit:delivery:streams",
            maxlen=30_000,
            expire_seconds=259_200,
        )
    )

    assert result == "1-0"
    assert len(DummyPipeline.instances) == 1
    pipeline = DummyPipeline.instances[0]
    assert pipeline.transaction is True
    assert pipeline.execute_count == 1
    assert pipeline.commands == [
        (
            "xadd",
            (),
            {
                "name": "audit:delivery:organization:org-id",
                "fields": {"event": "payload"},
                "maxlen": 30_000,
                "approximate": True,
            },
        ),
        (
            "expire",
            (),
            {
                "name": "audit:delivery:organization:org-id",
                "time": 259_200,
            },
        ),
        (
            "sadd",
            ("audit:delivery:streams", "audit:delivery:organization:org-id"),
            {},
        ),
    ]
