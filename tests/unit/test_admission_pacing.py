"""Tests for per-org workflow start pacing (``tracecat.admission.pacing``)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError

from tracecat.admission import pacing
from tracecat.admission.pacing import (
    StartPacer,
    StartRate,
    StartRateLimited,
    resolve_start_rate,
    start_rate_from_limits,
)
from tracecat.tiers.schemas import EffectiveLimits

# Real starts take a few milliseconds between calls; allow for that drift.
_TOLERANCE = timedelta(milliseconds=50)


def _limits(**values: int) -> EffectiveLimits:
    return EffectiveLimits.model_validate(values)


@pytest.fixture(autouse=True)
def clear_rate_cache() -> None:
    pacing._rate_cache.clear()


@pytest.fixture
async def redis_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[redis.Redis]:
    """Point the pacer at the test Redis database."""
    client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    wrapper = SimpleNamespace(_get_client=AsyncMock(return_value=client))
    monkeypatch.setattr(pacing, "get_redis_client", AsyncMock(return_value=wrapper))
    yield client
    await client.aclose()


def _use_rate(monkeypatch: pytest.MonkeyPatch, rate: StartRate | None) -> None:
    monkeypatch.setattr(pacing, "resolve_start_rate", AsyncMock(return_value=rate))


@pytest.mark.anyio
@pytest.mark.usefixtures("redis_client")
async def test_reserve_delay_grows_then_refuses_past_max_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Burst goes at once, then each start waits one more interval, then 429."""
    _use_rate(monkeypatch, StartRate(per_second=10, burst=2))
    pacer = StartPacer(max_delay=timedelta(milliseconds=250))
    org_id = uuid.uuid4()

    delays = [await pacer.reserve(org_id) for _ in range(4)]

    assert delays[0] == timedelta(0)
    assert delays[1] == timedelta(0)
    for got, want_ms in zip(delays[2:], [100, 200], strict=True):
        assert abs(got - timedelta(milliseconds=want_ms)) < _TOLERANCE

    with pytest.raises(StartRateLimited) as exc_info:
        await pacer.reserve(org_id)
    # The next slot is 300 ms out; it fits 250 ms after about 50 ms.
    assert exc_info.value.retry_after <= timedelta(milliseconds=50)
    assert exc_info.value.retry_after_seconds == 1
    assert exc_info.value.rate == 10
    assert exc_info.value.burst == 2


@pytest.mark.anyio
async def test_refused_start_does_not_take_a_slot(
    monkeypatch: pytest.MonkeyPatch, redis_client: redis.Redis
) -> None:
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    pacer = StartPacer(max_delay=timedelta(milliseconds=100))
    org_id = uuid.uuid4()

    assert await pacer.reserve(org_id) == timedelta(0)
    key = pacing._pacing_key(org_id)
    tat_after_first = await redis_client.get(key)
    for _ in range(3):
        with pytest.raises(StartRateLimited):
            await pacer.reserve(org_id)
    assert await redis_client.get(key) == tat_after_first
    assert 0 < await redis_client.pttl(key) <= 2000


@pytest.mark.anyio
@pytest.mark.usefixtures("redis_client")
async def test_refund_gives_back_one_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retried starts of runs that already exist don't use up the rate."""
    _use_rate(monkeypatch, StartRate(per_second=10, burst=1))
    pacer = StartPacer(max_delay=timedelta(milliseconds=150))
    org_id = uuid.uuid4()

    assert await pacer.reserve(org_id) == timedelta(0)
    first_delay = await pacer.reserve(org_id)
    assert abs(first_delay - timedelta(milliseconds=100)) < _TOLERANCE

    # Give back the second slot, then retry five duplicates: each reserves,
    # is rejected by Temporal, and refunds. None of them use up the rate.
    await pacer.refund(org_id)
    for _ in range(5):
        await pacer.reserve(org_id)
        await pacer.refund(org_id)

    again = await pacer.reserve(org_id)
    assert abs(again - timedelta(milliseconds=100)) < _TOLERANCE


@pytest.mark.anyio
async def test_refund_without_reservation_clears_nothing(
    monkeypatch: pytest.MonkeyPatch, redis_client: redis.Redis
) -> None:
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    org_id = uuid.uuid4()

    await StartPacer().refund(org_id)

    assert await redis_client.get(pacing._pacing_key(org_id)) is None


@pytest.mark.anyio
async def test_refund_with_redis_down_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    monkeypatch.setattr(
        pacing,
        "get_redis_client",
        AsyncMock(side_effect=RedisConnectionError("connection refused")),
    )

    await StartPacer().refund(uuid.uuid4())


@pytest.mark.anyio
@pytest.mark.usefixtures("redis_client")
async def test_orgs_are_paced_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    pacer = StartPacer(max_delay=timedelta(milliseconds=100))
    busy_org, other_org = uuid.uuid4(), uuid.uuid4()

    assert await pacer.reserve(busy_org) == timedelta(0)
    with pytest.raises(StartRateLimited):
        await pacer.reserve(busy_org)
    assert await pacer.reserve(other_org) == timedelta(0)


@pytest.mark.anyio
async def test_unlimited_org_makes_no_redis_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_rate(monkeypatch, None)
    get_client = AsyncMock(side_effect=AssertionError("Redis must not be called"))
    monkeypatch.setattr(pacing, "get_redis_client", get_client)

    assert await StartPacer().reserve(uuid.uuid4()) == timedelta(0)
    get_client.assert_not_awaited()


@pytest.mark.anyio
async def test_redis_down_gives_zero_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    monkeypatch.setattr(
        pacing,
        "get_redis_client",
        AsyncMock(side_effect=RedisConnectionError("connection refused")),
    )
    pacer = StartPacer()
    org_id = uuid.uuid4()

    for _ in range(3):
        assert await pacer.reserve(org_id) == timedelta(0)


@pytest.mark.anyio
async def test_redis_script_error_gives_zero_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    client = MagicMock()
    client.register_script.return_value = AsyncMock(
        side_effect=RedisConnectionError("connection reset")
    )
    wrapper = SimpleNamespace(_get_client=AsyncMock(return_value=client))
    monkeypatch.setattr(pacing, "get_redis_client", AsyncMock(return_value=wrapper))

    assert await StartPacer().reserve(uuid.uuid4()) == timedelta(0)


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        (_limits(), None),
        (_limits(api_rate_limit=0), None),
        (_limits(api_rate_limit=-5, api_burst_capacity=10), None),
        (_limits(api_rate_limit=5), StartRate(per_second=5, burst=5)),
        (
            _limits(api_rate_limit=5, api_burst_capacity=0),
            StartRate(per_second=5, burst=5),
        ),
        (
            _limits(api_rate_limit=5, api_burst_capacity=20),
            StartRate(per_second=5, burst=20),
        ),
        # A burst without a rate does nothing.
        (_limits(api_burst_capacity=20), None),
    ],
)
def test_start_rate_from_limits(
    limits: EffectiveLimits, expected: StartRate | None
) -> None:
    assert start_rate_from_limits(limits) == expected


class _FakeSession:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: Any) -> None:
        return None


@pytest.mark.anyio
async def test_resolve_start_rate_single_tenant_is_unpaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pacing.config, "TRACECAT__EE_MULTI_TENANT", False)
    session_factory = MagicMock(side_effect=AssertionError("no DB lookup"))
    monkeypatch.setattr(
        pacing, "get_async_session_tenant_context_manager", session_factory
    )

    assert await resolve_start_rate(uuid.uuid4()) is None
    session_factory.assert_not_called()


@pytest.mark.anyio
async def test_resolve_start_rate_caches_in_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pacing.config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(
        pacing, "get_async_session_tenant_context_manager", lambda **_: _FakeSession()
    )
    get_limits = AsyncMock(return_value=_limits(api_rate_limit=3, api_burst_capacity=6))
    monkeypatch.setattr(
        pacing,
        "TierService",
        lambda _session: SimpleNamespace(get_effective_limits=get_limits),
    )
    org_id = uuid.uuid4()

    first = await resolve_start_rate(org_id)
    second = await resolve_start_rate(org_id)

    assert first == second == StartRate(per_second=3, burst=6)
    get_limits.assert_awaited_once_with(org_id)


@pytest.mark.anyio
async def test_resolve_start_rate_lookup_failure_is_unpaced_and_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pacing.config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(
        pacing, "get_async_session_tenant_context_manager", lambda **_: _FakeSession()
    )
    get_limits = AsyncMock(side_effect=RuntimeError("database unavailable"))
    monkeypatch.setattr(
        pacing,
        "TierService",
        lambda _session: SimpleNamespace(get_effective_limits=get_limits),
    )
    org_id = uuid.uuid4()

    assert await resolve_start_rate(org_id) is None
    assert await resolve_start_rate(org_id) is None
    assert get_limits.await_count == 2
