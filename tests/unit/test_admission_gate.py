"""Admission gate behaviour with a mocked lease store and limit lookup."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat import config
from tracecat.admission import gate, limits
from tracecat.admission.gate import ConcurrencyBusyError, admit
from tracecat.admission.types import Admitted, Busy, ConcurrencyResource
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
    monkeypatch.setattr(gate, "FAIL_OPEN_AFTER_SECONDS", 0.05)
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
        pytest.raises(ConcurrencyBusyError) as exc_info,
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
    assert reasons == ["unavailable", "breaker_open"]


@pytest.mark.anyio
async def test_failed_limit_lookup_fails_open() -> None:
    with patch.object(
        gate, "resolve_limit", new=AsyncMock(side_effect=RuntimeError("db down"))
    ):
        assert await _admit() is None


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
