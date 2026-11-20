"""Tests for per-org workflow start pacing (``tracecat.admission.pacing``)."""

from __future__ import annotations

import asyncio
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
    UNPACED,
    StartPacer,
    StartRate,
    StartRateLimited,
    StartSlot,
    resolve_start_rate,
    start_rate_from_limits,
)
from tracecat.tiers.schemas import EffectiveLimits

# Real Redis calls take time between reservations. Intervals are a full
# second so slow CI workers stay well inside this margin.
_TOLERANCE = timedelta(milliseconds=300)


def _limits(**values: int) -> EffectiveLimits:
    return EffectiveLimits.model_validate(values)


@pytest.fixture(autouse=True)
def clear_rate_cache() -> None:
    pacing._rate_cache.clear()
    pacing._rate_lookups.clear()


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
    _use_rate(monkeypatch, StartRate(per_second=1, burst=2))
    pacer = StartPacer(max_delay=timedelta(milliseconds=2500))
    org_id = uuid.uuid4()

    delays = [(await pacer.reserve(org_id)).delay for _ in range(4)]

    assert delays[0] == timedelta(0)
    assert delays[1] == timedelta(0)
    for got, want_ms in zip(delays[2:], [1000, 2000], strict=True):
        assert abs(got - timedelta(milliseconds=want_ms)) < _TOLERANCE

    with pytest.raises(StartRateLimited) as exc_info:
        await pacer.reserve(org_id)
    # The next slot is 3 s out; it fits the 2.5 s maximum within 0.5 s.
    assert exc_info.value.retry_after <= timedelta(milliseconds=500)
    assert exc_info.value.retry_after_seconds == 1
    assert exc_info.value.rate == 1
    assert exc_info.value.burst == 2


@pytest.mark.anyio
async def test_refused_start_does_not_take_a_slot(
    monkeypatch: pytest.MonkeyPatch, redis_client: redis.Redis
) -> None:
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    pacer = StartPacer(max_delay=timedelta(milliseconds=100))
    org_id = uuid.uuid4()

    assert (await pacer.reserve(org_id)).delay == timedelta(0)
    key = pacing._pacing_key(org_id)
    tat_after_first = await redis_client.get(key)
    for _ in range(3):
        with pytest.raises(StartRateLimited):
            await pacer.reserve(org_id)
    assert await redis_client.get(key) == tat_after_first
    assert 0 < await redis_client.pttl(key) <= 2000


@pytest.mark.anyio
@pytest.mark.usefixtures("redis_client")
async def test_refund_gives_back_the_latest_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retried starts of runs that already exist don't use up the rate."""
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    pacer = StartPacer(max_delay=timedelta(milliseconds=1500))
    org_id = uuid.uuid4()

    assert (await pacer.reserve(org_id)).delay == timedelta(0)

    # Five duplicates in turn: each reserves, is rejected by Temporal, and
    # refunds while it is still the latest reservation.
    for _ in range(5):
        duplicate = await pacer.reserve(org_id)
        assert abs(duplicate.delay - timedelta(seconds=1)) < _TOLERANCE
        await pacer.refund(org_id, duplicate)

    again = await pacer.reserve(org_id)
    assert abs(again.delay - timedelta(seconds=1)) < _TOLERANCE


@pytest.mark.anyio
@pytest.mark.usefixtures("redis_client")
async def test_refund_after_a_later_reservation_keeps_the_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overlapping duplicates can't hand out a slot a later start holds."""
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    pacer = StartPacer(max_delay=timedelta(seconds=10))
    org_id = uuid.uuid4()

    first_duplicate = await pacer.reserve(org_id)
    second_duplicate = await pacer.reserve(org_id)
    runs = [await pacer.reserve(org_id)]
    await pacer.refund(org_id, first_duplicate)
    runs.append(await pacer.reserve(org_id))
    await pacer.refund(org_id, second_duplicate)
    runs.append(await pacer.reserve(org_id))

    # Each real run keeps its own slot, one interval apart.
    for got, want_ms in zip(runs, [2000, 3000, 4000], strict=True):
        assert abs(got.delay - timedelta(milliseconds=want_ms)) < _TOLERANCE


@pytest.mark.anyio
async def test_refund_of_an_unpaced_start_makes_no_redis_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_client = AsyncMock(side_effect=AssertionError("Redis must not be called"))
    monkeypatch.setattr(pacing, "get_redis_client", get_client)

    await StartPacer().refund(uuid.uuid4(), UNPACED)

    get_client.assert_not_awaited()


@pytest.mark.anyio
async def test_refund_with_redis_down_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pacing,
        "get_redis_client",
        AsyncMock(side_effect=RedisConnectionError("connection refused")),
    )
    slot = StartSlot(delay=timedelta(0), tat_us=1, interval_us=1)

    await StartPacer().refund(uuid.uuid4(), slot)


@pytest.mark.anyio
@pytest.mark.usefixtures("redis_client")
async def test_orgs_are_paced_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    pacer = StartPacer(max_delay=timedelta(milliseconds=100))
    busy_org, other_org = uuid.uuid4(), uuid.uuid4()

    assert (await pacer.reserve(busy_org)).delay == timedelta(0)
    with pytest.raises(StartRateLimited):
        await pacer.reserve(busy_org)
    assert (await pacer.reserve(other_org)).delay == timedelta(0)


@pytest.mark.anyio
async def test_unlimited_org_makes_no_redis_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_rate(monkeypatch, None)
    get_client = AsyncMock(side_effect=AssertionError("Redis must not be called"))
    monkeypatch.setattr(pacing, "get_redis_client", get_client)

    assert (await StartPacer().reserve(uuid.uuid4())).delay == timedelta(0)
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
        assert (await pacer.reserve(org_id)).delay == timedelta(0)


@pytest.mark.anyio
@pytest.mark.usefixtures("redis_client")
async def test_zero_max_delay_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero maximum admits the burst and refuses everything after it."""
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    pacer = StartPacer(max_delay=timedelta(0))
    org_id = uuid.uuid4()

    assert pacer.max_delay == timedelta(0)
    assert (await pacer.reserve(org_id)).delay == timedelta(0)
    with pytest.raises(StartRateLimited):
        await pacer.reserve(org_id)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [RuntimeError("Redis client not initialized"), ValueError("bad REDIS_URL")],
)
async def test_redis_client_setup_error_gives_zero_delay(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """A misconfigured Redis client fails open instead of failing starts."""
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    monkeypatch.setattr(pacing, "get_redis_client", AsyncMock(side_effect=error))

    slot = await StartPacer().reserve(uuid.uuid4())

    assert slot == UNPACED


@pytest.mark.anyio
async def test_slow_redis_client_setup_fails_open_within_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Client setup counts against the Redis deadline, not only the script."""
    _use_rate(monkeypatch, StartRate(per_second=1, burst=1))
    monkeypatch.setattr(pacing, "_REDIS_TIMEOUT_SECONDS", 0.05)

    async def _slow_client() -> object:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    monkeypatch.setattr(pacing, "get_redis_client", _slow_client)

    slot = await asyncio.wait_for(StartPacer().reserve(uuid.uuid4()), 1)

    assert slot == UNPACED


@pytest.mark.parametrize(
    ("elapsed", "remaining"),
    [(0.0, 2.0), (1.5, 0.5), (2.0, 0.0), (3.0, 0.0)],
)
def test_remaining_delay_subtracts_time_since_reservation(
    monkeypatch: pytest.MonkeyPatch, elapsed: float, remaining: float
) -> None:
    slot = StartSlot(delay=timedelta(seconds=2), reserved_at=100.0)
    monkeypatch.setattr(pacing.time, "monotonic", lambda: 100.0 + elapsed)

    assert slot.remaining_delay() == timedelta(seconds=remaining)


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

    assert (await StartPacer().reserve(uuid.uuid4())).delay == timedelta(0)


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


def _use_tier_lookup(monkeypatch: pytest.MonkeyPatch, get_limits: AsyncMock) -> None:
    monkeypatch.setattr(pacing.config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(
        pacing, "get_async_session_tenant_context_manager", lambda **_: _FakeSession()
    )
    monkeypatch.setattr(
        pacing,
        "TierService",
        lambda _session: SimpleNamespace(get_effective_limits=get_limits),
    )


@pytest.mark.anyio
async def test_resolve_start_rate_lookup_failure_is_unpaced_briefly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed lookup fails open and isn't retried by every start."""
    get_limits = AsyncMock(side_effect=RuntimeError("database unavailable"))
    _use_tier_lookup(monkeypatch, get_limits)
    org_id = uuid.uuid4()

    assert await resolve_start_rate(org_id) is None
    assert await resolve_start_rate(org_id) is None
    assert get_limits.await_count == 1

    # Once the short failure window passes, the next start looks again.
    pacing._rate_cache[org_id] = pacing._CachedRate(expires_at=0.0, rate=None)
    assert await resolve_start_rate(org_id) is None
    assert get_limits.await_count == 2


@pytest.mark.anyio
async def test_concurrent_cold_starts_share_one_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A burst on a cold cache takes one DB session, not one per start."""
    release = asyncio.Event()

    async def _slow_limits(_org_id: uuid.UUID) -> EffectiveLimits:
        await release.wait()
        return _limits(api_rate_limit=4)

    get_limits = AsyncMock(side_effect=_slow_limits)
    _use_tier_lookup(monkeypatch, get_limits)
    org_id = uuid.uuid4()

    waiters = [asyncio.create_task(resolve_start_rate(org_id)) for _ in range(10)]
    await asyncio.sleep(0)
    release.set()
    rates = await asyncio.gather(*waiters)

    assert rates == [StartRate(per_second=4, burst=4)] * 10
    get_limits.assert_awaited_once()
    assert pacing._rate_lookups == {}


@pytest.mark.anyio
async def test_slow_lookup_times_out_and_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lookup stuck waiting for a DB connection gives up quickly."""
    monkeypatch.setattr(pacing, "_LIMIT_LOOKUP_TIMEOUT_SECONDS", 0.05)

    async def _never(_org_id: uuid.UUID) -> EffectiveLimits:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    _use_tier_lookup(monkeypatch, AsyncMock(side_effect=_never))

    assert await asyncio.wait_for(resolve_start_rate(uuid.uuid4()), 1) is None
