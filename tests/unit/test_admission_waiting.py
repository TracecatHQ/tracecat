"""RunAdmission wait logic with a fake, deterministic workflow API."""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tracecat.admission import waiting
from tracecat.admission.types import (
    ConcurrencyBusyResult,
    ConcurrencyResource,
    parse_busy_result,
)
from tracecat.admission.waiting import RunAdmission, remaining_attempts_after_busy
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.workflow_logging import get_workflow_logger


@dataclass
class FakeWorkflow:
    """Stands in for ``temporalio.workflow`` inside ``waiting``."""

    clock: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    sleeps: list[float] = field(default_factory=list)
    on_sleep: Callable[[], None] | None = None
    _random: random.Random = field(default_factory=lambda: random.Random(0))

    def now(self) -> datetime:
        return self.clock

    def random(self) -> random.Random:
        return self._random

    def uuid4(self) -> uuid.UUID:
        return uuid.UUID(int=self._random.getrandbits(128), version=4)

    async def sleep(self, duration: timedelta) -> None:
        self.sleeps.append(duration.total_seconds())
        self.clock += duration
        if self.on_sleep is not None:
            self.on_sleep()
        await asyncio.sleep(0)

    async def wait_condition(
        self, fn: Callable[[], bool], *, timeout: timedelta | None = None
    ) -> None:
        spins = 0
        while not fn():
            if timeout is not None and spins >= 50:
                self.clock += timeout
                raise TimeoutError
            spins += 1
            await asyncio.sleep(0)


@pytest.fixture
def fake() -> Iterator[FakeWorkflow]:
    fake_workflow = FakeWorkflow()
    with patch.object(waiting, "workflow", fake_workflow):
        yield fake_workflow


def _admission() -> RunAdmission:
    return RunAdmission(
        resource=ConcurrencyResource.ACTIONS, logger=get_workflow_logger()
    )


def _busy(attempt: int = 1) -> dict[str, Any]:
    return ConcurrencyBusyResult(
        resource=ConcurrencyResource.ACTIONS, in_use=1, limit=1, attempt=attempt
    ).model_dump(mode="json")


@pytest.mark.parametrize(
    ("remaining", "busy_attempt", "expected"),
    [
        (3, 1, 3),  # first attempt was busy: nothing used
        (3, 2, 2),  # one real attempt failed before the busy one
        (3, 3, 1),
        (1, 1, 1),
        (0, 5, 0),  # unlimited stays unlimited
    ],
)
def test_remaining_attempts_after_busy(
    remaining: int, busy_attempt: int, expected: int
) -> None:
    assert remaining_attempts_after_busy(remaining, busy_attempt) == expected


@pytest.mark.anyio
async def test_attempts_are_preserved_across_bounces(fake: FakeWorkflow) -> None:
    results: list[Any] = [_busy(attempt=2), _busy(attempt=1), {"type": "inline"}]
    seen_attempts: list[int] = []

    async def dispatch(max_attempts: int, _probe_id: str | None) -> Any:
        seen_attempts.append(max_attempts)
        return results.pop(0)

    result = await _admission().execute(dispatch, max_attempts=3)

    assert result == {"type": "inline"}
    assert seen_attempts == [3, 2, 2]
    assert len(fake.sleeps) == 2


@pytest.mark.anyio
async def test_backoff_grows_and_caps_near_60_seconds(fake: FakeWorkflow) -> None:
    bounces = 12

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        return _busy() if len(fake.sleeps) < bounces else {"type": "inline"}

    await _admission().execute(dispatch, max_attempts=1)

    assert len(fake.sleeps) == bounces
    assert 1.6 <= fake.sleeps[0] <= 2.0
    assert fake.sleeps[1] > fake.sleeps[0]
    assert max(fake.sleeps) <= waiting.BACKOFF_MAX_SECONDS
    assert fake.sleeps[-1] >= 0.8 * waiting.BACKOFF_MAX_SECONDS


@pytest.mark.anyio
async def test_one_probe_per_run_while_blocked(fake: FakeWorkflow) -> None:
    """Waiting dispatches share one timer: one probe per period, not one each."""
    actions = 5
    free = 0
    in_flight = 0
    max_in_flight_after_first_wave = 0
    dispatches = 0

    def free_one_unit() -> None:
        nonlocal free
        free += 1

    fake.on_sleep = free_one_unit

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        nonlocal free, in_flight, dispatches, max_in_flight_after_first_wave
        dispatches += 1
        in_flight += 1
        if dispatches > actions:
            max_in_flight_after_first_wave = max(
                max_in_flight_after_first_wave, in_flight
            )
        await asyncio.sleep(0)
        in_flight -= 1
        if free == 0:
            return _busy()
        free -= 1
        return {"type": "inline", "data": dispatches}

    admission = _admission()
    results = await asyncio.gather(
        *(admission.execute(dispatch, max_attempts=1) for _ in range(actions))
    )

    assert len(results) == actions
    assert max_in_flight_after_first_wave == 1
    # One timer per freed unit, and at most one wasted probe per freed unit
    # (the hand-off probe that finds the org full again).
    assert len(fake.sleeps) == actions
    assert dispatches <= actions + 2 * actions


@pytest.mark.anyio
async def test_failed_probe_releases_the_token(fake: FakeWorkflow) -> None:
    calls = 0

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        nonlocal calls
        calls += 1
        if calls <= 2:
            return _busy()
        if calls == 3:
            raise RuntimeError("action failed")
        return {"type": "inline"}

    admission = _admission()
    first, second = await asyncio.gather(
        admission.execute(dispatch, max_attempts=1),
        admission.execute(dispatch, max_attempts=1),
        return_exceptions=True,
    )

    assert isinstance(first, RuntimeError) or isinstance(second, RuntimeError)
    assert {"type": "inline"} in (first, second)


@pytest.mark.anyio
async def test_uncontended_dispatch_has_no_timer(fake: FakeWorkflow) -> None:
    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        return {"type": "inline"}

    assert await _admission().execute(dispatch, max_attempts=3) == {"type": "inline"}
    assert fake.sleeps == []


@pytest.mark.anyio
async def test_long_wait_logs_once_and_keeps_waiting(fake: FakeWorkflow) -> None:
    log = MagicMock()
    admission = RunAdmission(resource=ConcurrencyResource.ACTIONS, logger=log)
    long_wait = timedelta(seconds=waiting.LONG_WAIT_SECONDS)
    start = fake.clock

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        # Busy for twice the long-wait threshold, then admitted.
        return _busy() if fake.clock - start < 2 * long_wait else {"type": "inline"}

    assert await admission.execute(dispatch, max_attempts=1) == {"type": "inline"}

    long_waits = [
        c for c in log.warning.call_args_list if c.args[0] == "concurrency.long_wait"
    ]
    assert len(long_waits) == 1
    assert long_waits[0].kwargs["wait_seconds"] >= waiting.LONG_WAIT_SECONDS
    admitted = [
        c
        for c in log.info.call_args_list
        if c.args[0] == "concurrency.admitted_after_wait"
    ]
    assert len(admitted) == 1


@pytest.mark.anyio
async def test_busy_result_survives_the_temporal_data_converter() -> None:
    """The converter drops unset fields; the busy tag must still arrive."""
    converter = get_data_converter(compression_enabled=False)
    busy = ConcurrencyBusyResult(
        resource=ConcurrencyResource.ACTIONS, in_use=2, limit=2, attempt=3
    )

    (decoded,) = await converter.decode(await converter.encode([busy]))

    assert decoded["type"] == "tracecat.concurrency.busy"
    assert parse_busy_result(decoded) == busy


def test_malformed_busy_payload_is_not_busy() -> None:
    assert parse_busy_result({"type": "tracecat.concurrency.busy"}) is None
    assert parse_busy_result({"type": "inline", "data": 1}) is None


async def _two_waiters_with_one_long_action(
    fake: FakeWorkflow, *, send_ack: bool
) -> tuple[list[str], asyncio.Event, list[asyncio.Task[Any]]]:
    """Block two dispatches, free two units, and hold the first admitted action.

    Returns the event log, the event that lets the first action finish, and the
    two execute tasks.
    """
    free = 0
    busy_answers = 0
    log: list[str] = []
    release_first = asyncio.Event()
    admission = RunAdmission(
        resource=ConcurrencyResource.ACTIONS,
        logger=get_workflow_logger(),
        acks_enabled=True,
    )

    def free_two_units() -> None:
        nonlocal free
        # Only once both dispatches are waiting, so neither gets in un-probed.
        if not log and busy_answers >= 2:
            free = 2

    fake.on_sleep = free_two_units

    async def dispatch(_max_attempts: int, probe_id: str | None) -> Any:
        nonlocal free, busy_answers
        await asyncio.sleep(0)
        if free == 0:
            busy_answers += 1
            return _busy()
        free -= 1
        index = sum(1 for entry in log if entry.startswith("start"))
        log.append(f"start {index}")
        if send_ack and probe_id is not None:
            admission.acknowledge(probe_id)  # what the executor's signal does
        if index == 0:
            await release_first.wait()  # a long-running admitted action
        log.append(f"finish {index}")
        return {"type": "inline", "data": index}

    tasks = [
        asyncio.create_task(admission.execute(dispatch, max_attempts=1))
        for _ in range(2)
    ]
    return log, release_first, tasks


@pytest.mark.anyio
async def test_admission_ack_hands_off_while_the_action_runs(
    fake: FakeWorkflow,
) -> None:
    """Two units free up: the second waiter starts while the first still runs."""
    log, release_first, tasks = await _two_waiters_with_one_long_action(
        fake, send_ack=True
    )
    async with asyncio.timeout(2):
        while "start 1" not in log:
            await asyncio.sleep(0)

    assert log == ["start 0", "start 1", "finish 1"]
    release_first.set()
    results = await asyncio.gather(*tasks)
    assert sorted(r["data"] for r in results) == [0, 1]


@pytest.mark.anyio
async def test_lost_admission_ack_still_hands_off_on_completion(
    fake: FakeWorkflow,
) -> None:
    """Without the signal, the token passes on when the probe completes."""
    log, release_first, tasks = await _two_waiters_with_one_long_action(
        fake, send_ack=False
    )
    for _ in range(200):
        await asyncio.sleep(0)
    assert log == ["start 0"]

    release_first.set()
    results = await asyncio.gather(*tasks)
    assert sorted(r["data"] for r in results) == [0, 1]
    assert log[:2] == ["start 0", "finish 0"]
