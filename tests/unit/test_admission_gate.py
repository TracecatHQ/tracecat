"""Admission gate behaviour with a mocked lease store and limit lookup."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from tracecat import config
from tracecat.admission import gate, limits
from tracecat.admission.gate import NotAdmittedError, admit
from tracecat.admission.leases import LeaseStore
from tracecat.admission.limits import LimitUnavailableError
from tracecat.admission.types import (
    Admitted,
    Busy,
    ConcurrencyResource,
    NotAdmittedReason,
)
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.tiers.schemas import EffectiveLimits

ORG_ID = uuid.UUID("00000000-0000-4000-8000-000000000222")
ACTIONS = ConcurrencyResource.ACTIONS


def _deadline() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=5)


@pytest.fixture(autouse=True)
def flag_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.WORKFLOW_CONCURRENCY_LIMITS}
    )
    # Wide enough that a slow CI runner still fits several attempts.
    monkeypatch.setattr(gate, "FAIL_OPEN_AFTER_SECONDS", 0.5)
    monkeypatch.setattr(gate, "RETRY_BACKOFF_SECONDS", 0.01)
    gate.reset_breaker()
    limits.clear_limits_cache()
    yield
    gate.reset_breaker()
    limits.clear_limits_cache()


def _store(admission: Admitted | Busy) -> MagicMock:
    store = MagicMock()
    store.try_acquire = AsyncMock(return_value=admission)
    store.renew = AsyncMock(return_value=True)
    store.release = AsyncMock(return_value=None)
    return store


async def _admit(units: int = 1) -> gate.Lease | None:
    return await gate.acquire(
        org_id=ORG_ID,
        resource=ACTIONS,
        lease_id="run:1:1",
        units=units,
        deadline=_deadline(),
    )


@pytest.mark.anyio
async def test_flag_off_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", set())
    resolve = AsyncMock()
    create = AsyncMock()
    with (
        patch.object(gate, "resolve_limit", new=resolve),
        patch.object(gate.LeaseStore, "create", new=create),
    ):
        assert await _admit() is None
    resolve.assert_not_awaited()
    create.assert_not_awaited()


@pytest.mark.anyio
async def test_unlimited_org_never_reaches_redis() -> None:
    create = AsyncMock()
    with (
        patch.object(gate, "resolve_limit", new=AsyncMock(return_value=None)),
        patch.object(gate.LeaseStore, "create", new=create),
    ):
        assert await _admit() is None
    create.assert_not_awaited()


@pytest.mark.anyio
async def test_units_are_clamped_to_the_limit() -> None:
    store = _store(Admitted(in_use=2, limit=2))
    with (
        patch.object(gate, "resolve_limit", new=AsyncMock(return_value=2)),
        patch.object(gate.LeaseStore, "create", new=AsyncMock(return_value=store)),
    ):
        lease = await _admit(units=8)
    assert lease is not None
    assert lease.units == 2
    assert store.try_acquire.await_args.kwargs["units"] == 2
    assert store.try_acquire.await_args.kwargs["limit"] == 2
    assert 0 < store.try_acquire.await_args.kwargs["ttl_seconds"] <= 90


@pytest.mark.anyio
async def test_busy_raises_with_usage() -> None:
    store = _store(Busy(in_use=3, limit=3))
    with (
        patch.object(gate, "resolve_limit", new=AsyncMock(return_value=3)),
        patch.object(gate.LeaseStore, "create", new=AsyncMock(return_value=store)),
        pytest.raises(NotAdmittedError) as exc_info,
    ):
        await _admit()
    assert exc_info.value.in_use == 3
    assert exc_info.value.limit == 3
    assert exc_info.value.resource is ACTIONS


@pytest.mark.anyio
async def test_redis_down_fails_open_then_breaker_skips_retries() -> None:
    create = AsyncMock(side_effect=ConnectionError("redis down"))
    log = MagicMock()
    with (
        patch.object(gate, "resolve_limit", new=AsyncMock(return_value=1)),
        patch.object(gate.LeaseStore, "create", new=create),
        patch.object(gate, "logger", new=log),
    ):
        first = await _admit()
        attempts_before_breaker = create.await_count
        second = await _admit()

    assert first is None
    assert second is None
    assert attempts_before_breaker >= 2
    # The breaker is open: the second call didn't try Redis again.
    assert create.await_count == attempts_before_breaker
    events = [c.args[0] for c in log.warning.call_args_list]
    assert events.count("concurrency.fail_open") == 2
    reasons = [c.kwargs["reason"] for c in log.warning.call_args_list]
    assert reasons == ["store_unavailable", "breaker_open"]


@pytest.mark.anyio
async def test_failed_limit_lookup_defers_without_the_breaker() -> None:
    """A lookup failure never fails open and never stops enforcement for others."""
    store = _store(Admitted(in_use=1, limit=5))
    create = AsyncMock(return_value=store)
    resolve = AsyncMock(side_effect=LimitUnavailableError("db down"))
    with (
        patch.object(gate, "resolve_limit", new=resolve),
        patch.object(gate.LeaseStore, "create", new=create),
        pytest.raises(NotAdmittedError) as exc_info,
    ):
        await _admit()
    assert exc_info.value.reason is NotAdmittedReason.LIMIT_UNAVAILABLE
    assert (exc_info.value.in_use, exc_info.value.limit) == (-1, -1)
    resolve.assert_awaited_once()
    create.assert_not_awaited()
    # The breaker stayed closed: the next call is enforced.
    with (
        patch.object(gate, "resolve_limit", new=AsyncMock(return_value=5)),
        patch.object(gate.LeaseStore, "create", new=create),
    ):
        assert await _admit() is not None


def _pool_full_store(*, exhausted: bool) -> MagicMock:
    store = _store(Admitted(in_use=1, limit=5))
    store.try_acquire = AsyncMock(
        side_effect=RedisConnectionError("Too many connections")
    )
    store.pool_exhausted = MagicMock(return_value=exhausted)
    return store


def _timeout_store(error: Exception) -> MagicMock:
    store = _store(Admitted(in_use=1, limit=5))
    store.try_acquire = AsyncMock(side_effect=error)
    store.pool_exhausted = MagicMock(return_value=False)
    return store


@pytest.mark.parametrize(
    ("store_factory", "lag", "local"),
    [
        pytest.param(
            lambda: _timeout_store(TimeoutError()), 0.5, True, id="timeout-high-lag"
        ),
        pytest.param(
            lambda: _timeout_store(TimeoutError()), 0.01, False, id="timeout-low-lag"
        ),
        pytest.param(
            lambda: _timeout_store(TimeoutError()), None, False, id="timeout-no-sampler"
        ),
        pytest.param(
            lambda: _timeout_store(RedisTimeoutError("socket")),
            0.5,
            True,
            id="redis-timeout-high-lag",
        ),
        pytest.param(
            lambda: _timeout_store(RedisTimeoutError("socket")),
            0.01,
            False,
            id="redis-timeout-low-lag",
        ),
        pytest.param(
            lambda: _timeout_store(RedisConnectionError("refused")),
            0.5,
            False,
            id="error-high-lag",
        ),
        pytest.param(
            lambda: _timeout_store(RedisConnectionError("refused")),
            0.01,
            False,
            id="error-low-lag",
        ),
        pytest.param(
            lambda: _pool_full_store(exhausted=True), 0.01, True, id="pool-exhausted"
        ),
        pytest.param(
            lambda: _pool_full_store(exhausted=False), 0.01, False, id="pool-not-full"
        ),
    ],
)
@pytest.mark.anyio
async def test_classifier_tells_overload_from_outage(
    store_factory: Callable[[], MagicMock], lag: float | None, local: bool
) -> None:
    store = store_factory()
    lag_sampler = MagicMock()
    lag_sampler.max_lag_seconds = MagicMock(return_value=lag)
    log = MagicMock()
    with (
        patch.object(gate, "resolve_limit", new=AsyncMock(return_value=5)),
        patch.object(gate.LeaseStore, "create", new=AsyncMock(return_value=store)),
        patch.object(gate, "loop_lag", new=lag_sampler),
        patch.object(gate, "logger", new=log),
    ):
        if local:
            with pytest.raises(NotAdmittedError) as exc_info:
                await _admit(units=2)
            error = exc_info.value
            assert error.reason is NotAdmittedReason.EXECUTOR_OVERLOADED
            assert (error.in_use, error.limit) == (-1, -1)
            # No retry wait: one attempt, then defer.
            assert store.try_acquire.await_count == 1
            # Local overload never moves the shared breaker.
            assert gate._breaker_open_until == 0.0
            events = [c.args[0] for c in log.warning.call_args_list]
            assert "concurrency.fail_open" not in events
        else:
            # An outage keeps today's behaviour: retry, fail open, open the breaker.
            assert await _admit(units=2) is None
            assert store.try_acquire.await_count >= 2
            assert gate._breaker_open_until > 0.0
    # Either way a lease whose reply was lost is released by attempt ID.
    store.release.assert_awaited_once_with(ORG_ID, ACTIONS, "run:1:1", units=2)


@pytest.mark.anyio
async def test_overload_deferral_leaves_the_next_call_enforced() -> None:
    lag_sampler = MagicMock()
    lag_sampler.max_lag_seconds = MagicMock(return_value=1.0)
    with (
        patch.object(gate, "resolve_limit", new=AsyncMock(return_value=5)),
        patch.object(
            gate.LeaseStore,
            "create",
            new=AsyncMock(return_value=_timeout_store(TimeoutError())),
        ),
        patch.object(gate, "loop_lag", new=lag_sampler),
        pytest.raises(NotAdmittedError),
    ):
        await _admit()
    store = _store(Admitted(in_use=1, limit=5))
    with (
        patch.object(gate, "resolve_limit", new=AsyncMock(return_value=5)),
        patch.object(gate.LeaseStore, "create", new=AsyncMock(return_value=store)),
    ):
        assert await _admit() is not None
    store.try_acquire.assert_awaited_once()


@pytest.mark.anyio
async def test_giving_up_on_redis_releases_a_possibly_taken_lease() -> None:
    """The acquire may have run with its reply lost; release it on the way out."""
    store = _store(Admitted(in_use=1, limit=5))
    store.try_acquire = AsyncMock(side_effect=TimeoutError("reply lost"))
    with (
        patch.object(gate, "resolve_limit", new=AsyncMock(return_value=5)),
        patch.object(gate.LeaseStore, "create", new=AsyncMock(return_value=store)),
    ):
        assert await _admit(units=2) is None
    store.release.assert_awaited_once_with(ORG_ID, ACTIONS, "run:1:1", units=2)


@pytest.mark.anyio
async def test_non_positive_limit_runs_uncounted() -> None:
    create = AsyncMock()
    with (
        patch.object(gate, "resolve_limit", new=AsyncMock(return_value=0)),
        patch.object(gate.LeaseStore, "create", new=create),
    ):
        assert await _admit() is None
    create.assert_not_awaited()


@pytest.mark.anyio
async def test_admit_releases_after_body_error() -> None:
    store = _store(Admitted(in_use=1, limit=5))

    async def run_failing_body() -> None:
        async with admit(
            org_id=ORG_ID,
            resource=ACTIONS,
            lease_id="run:1:1",
            units=1,
            deadline=_deadline(),
        ):
            raise ValueError("action failed")

    with (
        patch.object(gate, "resolve_limit", new=AsyncMock(return_value=5)),
        patch.object(gate.LeaseStore, "create", new=AsyncMock(return_value=store)),
        pytest.raises(ValueError),
    ):
        await run_failing_body()
    store.release.assert_awaited_once_with(ORG_ID, ACTIONS, "run:1:1", units=1)


@pytest.mark.anyio
async def test_release_and_renew_failures_never_raise() -> None:
    store = _store(Admitted(in_use=1, limit=5))
    store.release = AsyncMock(side_effect=ConnectionError("redis down"))
    store.renew = AsyncMock(side_effect=ConnectionError("redis down"))
    with (
        patch.object(gate, "resolve_limit", new=AsyncMock(return_value=5)),
        patch.object(gate.LeaseStore, "create", new=AsyncMock(return_value=store)),
    ):
        lease = await _admit()
    assert lease is not None

    await lease.renew()
    await lease.release()

    store.renew.assert_awaited_once()
    store.release.assert_awaited_once()


@pytest.mark.anyio
async def test_limits_are_cached_per_process() -> None:
    effective = EffectiveLimits(
        api_rate_limit=None,
        api_burst_capacity=None,
        max_concurrent_workflows=None,
        max_action_executions_per_workflow=None,
        max_concurrent_actions=4,
    )
    service = MagicMock()
    service.get_effective_limits = AsyncMock(return_value=effective)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=None)
    with (
        patch.object(
            limits,
            "get_async_session_tenant_context_manager",
            return_value=session_cm,
        ) as session_factory,
        patch.object(limits, "TierService", return_value=service),
    ):
        assert await limits.resolve_limit(ORG_ID, ACTIONS) == 4
        assert await limits.resolve_limit(ORG_ID, ACTIONS) == 4
        assert (
            await limits.resolve_limit(ORG_ID, ConcurrencyResource.TOOL_CALLS) is None
        )

    session_factory.assert_called_once_with(organization_id=ORG_ID)
    service.get_effective_limits.assert_awaited_once_with(ORG_ID)


@pytest.mark.anyio
async def test_limits_cache_honours_the_configured_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS", 0)
    effective = EffectiveLimits(
        api_rate_limit=None,
        api_burst_capacity=None,
        max_concurrent_workflows=None,
        max_action_executions_per_workflow=None,
        max_concurrent_actions=4,
    )
    service = MagicMock()
    service.get_effective_limits = AsyncMock(return_value=effective)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=None)
    with (
        patch.object(
            limits, "get_async_session_tenant_context_manager", return_value=session_cm
        ),
        patch.object(limits, "TierService", return_value=service),
    ):
        await limits.resolve_limit(ORG_ID, ACTIONS)
        await limits.resolve_limit(ORG_ID, ACTIONS)

    # TTL 0 disables the cache: every call reads the tier.
    assert service.get_effective_limits.await_count == 2


def _effective(max_concurrent_actions: int | None) -> EffectiveLimits:
    return EffectiveLimits(
        api_rate_limit=None,
        api_burst_capacity=None,
        max_concurrent_workflows=None,
        max_action_executions_per_workflow=None,
        max_concurrent_actions=max_concurrent_actions,
    )


@dataclass
class FakeClock:
    now: float = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(limits, "monotonic", fake)
    monkeypatch.setattr(config, "TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS", 30)
    return fake


@pytest.mark.anyio
async def test_concurrent_cache_misses_share_one_read(clock: FakeClock) -> None:
    del clock
    release = asyncio.Event()
    reads = 0

    async def read(org_id: uuid.UUID) -> EffectiveLimits:
        nonlocal reads
        del org_id
        reads += 1
        await release.wait()
        return _effective(4)

    with patch.object(limits, "_read", new=read):
        waiters = [
            asyncio.create_task(limits.resolve_limit(ORG_ID, ACTIONS))
            for _ in range(20)
        ]
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(*waiters)
    assert results == [4] * 20
    assert reads == 1


@pytest.mark.anyio
async def test_a_timed_out_caller_leaves_the_shared_read_running(
    clock: FakeClock,
) -> None:
    del clock
    release = asyncio.Event()
    reads = 0

    async def read(org_id: uuid.UUID) -> EffectiveLimits:
        nonlocal reads
        del org_id
        reads += 1
        await release.wait()
        return _effective(4)

    with patch.object(limits, "_read", new=read):
        with pytest.raises(LimitUnavailableError):
            await limits.resolve_limit(ORG_ID, ACTIONS, timeout_seconds=0.01)
        patient = asyncio.create_task(limits.resolve_limit(ORG_ID, ACTIONS))
        await asyncio.sleep(0)
        release.set()
        assert await patient == 4
    assert reads == 1


@pytest.mark.anyio
async def test_stale_limit_is_served_for_one_more_ttl_then_deferred(
    clock: FakeClock,
) -> None:
    effective = _effective(4)
    service = MagicMock()
    service.get_effective_limits = AsyncMock(return_value=effective)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=None)
    with (
        patch.object(
            limits, "get_async_session_tenant_context_manager", return_value=session_cm
        ),
        patch.object(limits, "TierService", return_value=service),
    ):
        assert await limits.resolve_limit(ORG_ID, ACTIONS) == 4

        service.get_effective_limits.side_effect = RuntimeError("db down")
        clock.now += 45  # Expired, inside the one-TTL grace.
        assert await limits.resolve_limit(ORG_ID, ACTIONS) == 4

        clock.now += 20  # 65 s old: past two TTLs.
        with pytest.raises(LimitUnavailableError):
            await limits.resolve_limit(ORG_ID, ACTIONS)

        # A refresh that succeeds again serves the new value.
        service.get_effective_limits.side_effect = None
        service.get_effective_limits.return_value = _effective(2)
        assert await limits.resolve_limit(ORG_ID, ACTIONS) == 2


@pytest.mark.anyio
async def test_lookup_failure_without_a_cached_limit_is_unavailable(
    clock: FakeClock,
) -> None:
    del clock

    async def read(org_id: uuid.UUID) -> EffectiveLimits:
        raise RuntimeError("db down")

    with (
        patch.object(limits, "_read", new=read),
        pytest.raises(LimitUnavailableError),
    ):
        await limits.resolve_limit(ORG_ID, ACTIONS)


@pytest.mark.anyio
async def test_stale_grace_is_judged_when_the_lookup_fails(clock: FakeClock) -> None:
    """A lookup that starts inside the grace but fails after it must not serve stale."""
    limits._cache[ORG_ID] = limits._CachedLimits(
        limits=_effective(10), fetched_at=clock.now - 59.9
    )

    async def slow_failure(org_id: uuid.UUID) -> EffectiveLimits:
        del org_id
        clock.now += 1.1  # Now 61 s old: past two TTLs.
        raise RuntimeError("db down")

    with (
        patch.object(limits, "_read", new=slow_failure),
        pytest.raises(LimitUnavailableError),
    ):
        await limits.resolve_limit(ORG_ID, ACTIONS)


@pytest.mark.anyio
async def test_a_stalled_shared_read_is_bounded(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung database read ends on its own, so the next caller reads afresh."""
    del clock
    monkeypatch.setattr(limits, "READ_TIMEOUT_SECONDS", 0.05)
    service = MagicMock()
    hang = asyncio.Event()

    async def hung_read(org_id: uuid.UUID) -> EffectiveLimits:
        del org_id
        await hang.wait()
        return _effective(4)

    service.get_effective_limits = hung_read
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=None)
    with (
        patch.object(
            limits, "get_async_session_tenant_context_manager", return_value=session_cm
        ),
        patch.object(limits, "TierService", return_value=service),
    ):
        with pytest.raises(LimitUnavailableError):
            await limits.resolve_limit(ORG_ID, ACTIONS, timeout_seconds=1.0)
        assert ORG_ID not in limits._in_flight
        # The session was exited, releasing its connection.
        session_cm.__aexit__.assert_awaited_once()

        service.get_effective_limits = AsyncMock(return_value=_effective(4))
        assert await limits.resolve_limit(ORG_ID, ACTIONS) == 4


def test_pool_check_never_raises_on_an_unexpected_pool() -> None:
    client = MagicMock()
    client.connection_pool = object()  # No redis-py internals.
    store = LeaseStore(client)
    assert store.pool_exhausted() is False
