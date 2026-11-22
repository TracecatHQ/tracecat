"""Unit tests for in-process admission waits (live agent tool calls)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tracecat.admission import live
from tracecat.admission.live import CapacityBusyError, holding, run_with_capacity_wait
from tracecat.admission.types import (
    UNKNOWN_COUNT,
    ConcurrencyResource,
    NotAdmitted,
    NotAdmittedReason,
)

ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
RESOURCE = ConcurrencyResource.TOOL_CALLS


class FakeStore:
    """Lease store whose ``in_use`` answers come from a list."""

    def __init__(self, counts: list[int | Exception]) -> None:
        self.counts = counts
        self.reads = 0

    async def in_use(self, org_id: uuid.UUID, resource: ConcurrencyResource) -> int:
        assert (org_id, resource) == (ORG_ID, RESOURCE)
        self.reads += 1
        answer = self.counts.pop(0) if len(self.counts) > 1 else self.counts[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@dataclass
class Harness:
    """Flag on, a configurable limit and store, and a captured logger."""

    monkeypatch: pytest.MonkeyPatch
    logger: MagicMock
    resolve_limit: AsyncMock = field(default_factory=AsyncMock)
    create_store: AsyncMock = field(default_factory=AsyncMock)

    def __call__(
        self, *, limit: int | None, counts: list[int | Exception]
    ) -> FakeStore:
        store = FakeStore(counts)
        self.resolve_limit.return_value = limit
        self.create_store.return_value = store
        self.monkeypatch.setattr(live, "resolve_limit", self.resolve_limit)
        self.monkeypatch.setattr(live.LeaseStore, "create", self.create_store)
        return store


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> Harness:
    monkeypatch.setattr(live, "is_feature_enabled", lambda _flag: True)
    monkeypatch.setattr(live, "POLL_INTERVAL_SECONDS", 0.01)
    logger = MagicMock()
    monkeypatch.setattr(live, "logger", logger)
    return Harness(monkeypatch=monkeypatch, logger=logger)


def _not_admitted() -> NotAdmitted:
    return NotAdmitted(resource=RESOURCE, in_use=1, limit=1, attempt=1)


async def _wait(run: AsyncMock, *, wait_seconds: float = 1.0) -> Any:
    return await run_with_capacity_wait(
        run, org_id=ORG_ID, resource=RESOURCE, wait_seconds=wait_seconds
    )


@pytest.mark.anyio
async def test_unlimited_runs_without_touching_redis(setup: Harness) -> None:
    store = setup(limit=None, counts=[0])
    run = AsyncMock(return_value="ok")

    assert await _wait(run) == "ok"
    assert run.await_count == 1
    assert store.reads == 0
    setup.create_store.assert_not_awaited()


@pytest.mark.anyio
async def test_flag_off_skips_the_limit_lookup(
    setup: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup(limit=1, counts=[5])
    monkeypatch.setattr(live, "is_feature_enabled", lambda _flag: False)
    run = AsyncMock(return_value="ok")

    assert await _wait(run) == "ok"
    setup.resolve_limit.assert_not_awaited()


@pytest.mark.anyio
async def test_waits_while_full_then_runs(setup: Harness) -> None:
    store = setup(limit=1, counts=[1, 1, 0])
    run = AsyncMock(return_value="ok")

    assert await _wait(run) == "ok"
    assert run.await_count == 1
    assert store.reads == 3
    setup.logger.info.assert_called_once()
    assert setup.logger.info.call_args.args[0] == "concurrency.admitted_after_wait"


@pytest.mark.anyio
async def test_wait_time_excludes_the_call_itself(setup: Harness) -> None:
    setup(limit=1, counts=[1, 0])

    loop = asyncio.get_running_loop()
    call_started_at: list[float] = []

    async def slow_call() -> str:
        call_started_at.append(loop.time())
        await asyncio.sleep(0.05)
        return "ok"

    before = loop.time()
    assert await _wait(AsyncMock(side_effect=slow_call)) == "ok"
    wait_seconds = setup.logger.info.call_args.kwargs["wait_seconds"]
    # Monotonic clock: the wait ends before the call starts, so it can never
    # exceed the time from entering the wait to the call's start.
    assert 0 < wait_seconds <= call_started_at[0] - before


@pytest.mark.anyio
async def test_lost_race_runs_again(setup: Harness) -> None:
    setup(limit=1, counts=[0])
    run = AsyncMock(side_effect=[_not_admitted(), "ok"])

    assert await _wait(run) == "ok"
    assert run.await_count == 2


@pytest.mark.anyio
async def test_gives_up_with_capacity_busy_after_the_wait(setup: Harness) -> None:
    setup(limit=2, counts=[2])
    run = AsyncMock(return_value="ok")

    with pytest.raises(CapacityBusyError) as exc_info:
        await _wait(run, wait_seconds=0.05)

    assert run.await_count == 0
    error = exc_info.value
    assert (error.resource, error.in_use, error.limit) == (RESOURCE, 2, 2)
    assert error.waited_seconds >= 0.04
    setup.logger.warning.assert_called_once()
    assert setup.logger.warning.call_args.args[0] == "concurrency.overload"
    fields = setup.logger.warning.call_args.kwargs
    assert fields["org_id"] == str(ORG_ID)
    assert fields["resource"] == RESOURCE.value
    assert (fields["in_use"], fields["limit"]) == (2, 2)


@pytest.mark.anyio
async def test_not_admitted_until_the_end_gives_up(setup: Harness) -> None:
    setup(limit=1, counts=[0])
    run = AsyncMock(return_value=_not_admitted())

    with pytest.raises(CapacityBusyError):
        await _wait(run, wait_seconds=0.05)
    assert run.await_count >= 1


@pytest.mark.anyio
async def test_store_error_lets_the_gate_decide(setup: Harness) -> None:
    setup(limit=1, counts=[ConnectionError("redis down")])
    run = AsyncMock(return_value="ok")

    assert await _wait(run) == "ok"
    assert run.await_count == 1


@pytest.mark.anyio
async def test_holding_renews_and_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live, "RENEW_INTERVAL_SECONDS", 0.01)
    lease = MagicMock()
    lease.renew = AsyncMock()
    lease.release = AsyncMock()

    async with holding(lease):
        await asyncio.sleep(0.05)

    assert lease.renew.await_count >= 2
    lease.release.assert_awaited_once()


@pytest.mark.anyio
async def test_holding_releases_when_the_body_raises() -> None:
    lease = MagicMock()
    lease.renew = AsyncMock()
    lease.release = AsyncMock()

    with pytest.raises(RuntimeError):
        async with holding(lease):
            raise RuntimeError("turn failed")

    lease.release.assert_awaited_once()


@pytest.mark.anyio
async def test_holding_nothing_is_a_no_op() -> None:
    async with holding(None):
        pass


@pytest.mark.anyio
async def test_nan_wait_gives_up_instead_of_spinning(setup: Harness) -> None:
    setup(limit=1, counts=[1])
    run = AsyncMock(return_value="ok")

    with pytest.raises(CapacityBusyError):
        await _wait(run, wait_seconds=float("nan"))
    run.assert_not_awaited()


@pytest.mark.anyio
async def test_overloaded_executor_gives_up_with_a_sane_message(
    setup: Harness,
) -> None:
    """Deferred attempts carry -1 counts; the error must not show them."""
    setup(limit=1, counts=[0])
    deferred = NotAdmitted(
        resource=RESOURCE,
        in_use=UNKNOWN_COUNT,
        limit=UNKNOWN_COUNT,
        attempt=1,
        reason=NotAdmittedReason.EXECUTOR_OVERLOADED,
    )
    run = AsyncMock(return_value=deferred)

    with pytest.raises(CapacityBusyError) as exc_info:
        await _wait(run, wait_seconds=0.05)

    error = exc_info.value
    assert error.reason == NotAdmittedReason.EXECUTOR_OVERLOADED
    assert "-1" not in str(error)
    # The live budget is kept: it retried until the wait ran out.
    assert run.await_count >= 2
