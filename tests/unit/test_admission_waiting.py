"""RunAdmission wait logic with a fake, deterministic workflow API."""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from tracecat.admission import waiting
from tracecat.admission.types import (
    UNKNOWN_COUNT,
    ConcurrencyResource,
    NotAdmitted,
    NotAdmittedReason,
    parse_not_admitted,
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
    free_sleeps: int = 1_000_000
    """Sleeps beyond this many wait for ``sleep_hold``."""
    sleep_hold: asyncio.Event = field(default_factory=asyncio.Event)
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
        if len(self.sleeps) > self.free_sleeps:
            await self.sleep_hold.wait()
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
    return NotAdmitted(
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
async def test_not_admitted_result_survives_the_temporal_data_converter() -> None:
    """The converter drops unset fields; the not-admitted tag must still arrive."""
    converter = get_data_converter(compression_enabled=False)
    busy = NotAdmitted(
        resource=ConcurrencyResource.ACTIONS, in_use=2, limit=2, attempt=3
    )

    (decoded,) = await converter.decode(await converter.encode([busy]))

    assert decoded["type"] == "tracecat.admission.not_admitted"
    assert parse_not_admitted(decoded) == busy


class _NotAdmittedBeforeReason(BaseModel):
    """``NotAdmitted`` as readers before ``reason`` (ENG-2619) parse it."""

    type: Literal["tracecat.admission.not_admitted"]
    resource: ConcurrencyResource
    in_use: int
    limit: int
    attempt: int


def _deferred(attempt: int = 1) -> NotAdmitted:
    return NotAdmitted(
        resource=ConcurrencyResource.ACTIONS,
        in_use=UNKNOWN_COUNT,
        limit=UNKNOWN_COUNT,
        attempt=attempt,
        reason=NotAdmittedReason.EXECUTOR_OVERLOADED,
    )


@pytest.mark.anyio
async def test_deferred_result_parses_for_new_and_old_readers() -> None:
    """Executors may emit ``reason`` before every workflow worker knows it."""
    converter = get_data_converter(compression_enabled=False)
    (decoded,) = await converter.decode(await converter.encode([_deferred()]))

    parsed = parse_not_admitted(decoded)
    assert parsed is not None
    assert parsed.reason == NotAdmittedReason.EXECUTOR_OVERLOADED
    assert (parsed.in_use, parsed.limit) == (-1, -1)

    # A reader from before ``reason`` still sees a not-admitted result.
    old = _NotAdmittedBeforeReason.model_validate(decoded)
    assert (old.in_use, old.limit, old.attempt) == (-1, -1, 1)


def test_not_admitted_reason_defaults_and_tolerates_unknown_values() -> None:
    at_limit = {
        "type": "tracecat.admission.not_admitted",
        "resource": "actions",
        "in_use": 3,
        "limit": 3,
        "attempt": 1,
    }
    parsed = parse_not_admitted(at_limit)
    assert parsed is not None
    assert parsed.reason == NotAdmittedReason.AT_LIMIT

    future = parse_not_admitted({**at_limit, "reason": "some_future_reason"})
    assert future is not None
    assert future.reason == "some_future_reason"


@pytest.mark.anyio
async def test_deferred_results_back_off_without_using_attempts(
    fake: FakeWorkflow,
) -> None:
    deferred = _deferred().model_dump(mode="json")
    results: list[Any] = [deferred, deferred, {"type": "inline"}]
    seen_attempts: list[int] = []

    async def dispatch(max_attempts: int, _probe_id: str | None) -> Any:
        seen_attempts.append(max_attempts)
        return results.pop(0)

    result = await _admission().execute(dispatch, max_attempts=2)

    assert result == {"type": "inline"}
    assert seen_attempts == [2, 2, 2]
    assert len(fake.sleeps) == 2


def test_malformed_not_admitted_payload_is_ignored() -> None:
    assert parse_not_admitted({"type": "tracecat.admission.not_admitted"}) is None
    assert parse_not_admitted({"type": "inline", "data": 1}) is None


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


@pytest.mark.anyio
async def test_older_completion_keeps_newer_contention(fake: FakeWorkflow) -> None:
    """An acked action finishing later must not unblock a run that is busy again."""
    admission = RunAdmission(
        resource=ConcurrencyResource.ACTIONS,
        logger=get_workflow_logger(),
        acks_enabled=True,
    )
    free = 0
    busy_answers = 0
    freed = False
    release_a = asyncio.Event()
    started: list[str] = []

    def free_one_unit_once() -> None:
        nonlocal free, freed
        if not freed and busy_answers >= 2:
            freed = True
            free = 1
            # The next backoff sleep (B's) waits for sleep_hold.
            fake.free_sleeps = len(fake.sleeps)

    fake.on_sleep = free_one_unit_once

    async def dispatch(_max_attempts: int, probe_id: str | None) -> Any:
        nonlocal free, busy_answers
        await asyncio.sleep(0)
        if free == 0:
            busy_answers += 1
            return _busy()
        free -= 1
        name = "a" if not started else "b"
        started.append(name)
        if probe_id is not None:
            admission.acknowledge(probe_id)
        if name == "a":
            await release_a.wait()
        return {"type": "inline", "data": name}

    tasks = [
        asyncio.create_task(admission.execute(dispatch, max_attempts=1))
        for _ in range(2)
    ]
    # A is admitted and acked; B probes at once, is busy, and waits on its timer.
    async with asyncio.timeout(2):
        while not (freed and len(fake.sleeps) > fake.free_sleeps):
            await asyncio.sleep(0)
    assert started == ["a"]

    release_a.set()
    for _ in range(50):
        await asyncio.sleep(0)
    # A's completion said nothing about B's newer not-admitted answer.
    assert admission._blocked is True  # pyright: ignore[reportPrivateUsage]
    assert admission._needs_timer is True  # pyright: ignore[reportPrivateUsage]

    free = 1
    fake.sleep_hold.set()
    results = await asyncio.gather(*tasks)
    assert sorted(r["data"] for r in results) == ["a", "b"]


def test_late_ack_for_a_finished_probe_is_ignored() -> None:
    admission = RunAdmission(
        resource=ConcurrencyResource.ACTIONS,
        logger=get_workflow_logger(),
        acks_enabled=True,
    )
    admission.acknowledge("admission-probe-already-done")
    assert admission._acked == set()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.anyio
async def test_interrupt_ends_the_backoff_early(fake: FakeWorkflow) -> None:
    results: list[Any] = [_busy(), {"type": "inline"}]
    started_at = fake.clock
    checks = 0

    def cancelled() -> bool:
        # False when the wait starts; a user cancel arrives a few polls later.
        nonlocal checks
        checks += 1
        return checks > 3

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        return results.pop(0)

    result = await _admission().execute(dispatch, max_attempts=1, interrupt=cancelled)

    assert result == {"type": "inline"}
    assert checks > 3  # the interrupt was observed during the wait
    assert fake.sleeps == []  # no plain timer
    assert fake.clock == started_at  # the backoff never ran out


@pytest.mark.anyio
async def test_interrupt_that_stays_false_waits_out_the_backoff(
    fake: FakeWorkflow,
) -> None:
    results: list[Any] = [_busy(), {"type": "inline"}]
    started_at = fake.clock

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        return results.pop(0)

    result = await _admission().execute(
        dispatch, max_attempts=1, interrupt=lambda: False
    )

    assert result == {"type": "inline"}
    assert fake.sleeps == []
    assert fake.clock > started_at  # the timed wait ran to its timeout


@pytest.mark.anyio
async def test_interrupt_ends_only_one_backoff(fake: FakeWorkflow) -> None:
    """A dispatch that ignores the interrupt still waits on timers after it."""
    results: list[Any] = [_busy(), _busy(), _busy(), {"type": "inline"}]

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        return results.pop(0)

    result = await _admission().execute(
        dispatch, max_attempts=1, interrupt=lambda: True
    )

    assert result == {"type": "inline"}
    assert len(fake.sleeps) == 2  # first backoff interrupted, the rest are timers


def _waking_admission() -> RunAdmission:
    return RunAdmission(
        resource=ConcurrencyResource.ACTIONS,
        logger=get_workflow_logger(),
        acks_enabled=True,
        wake_enabled=True,
    )


@pytest.mark.anyio
async def test_wake_ends_the_sole_waiters_backoff(fake: FakeWorkflow) -> None:
    admission = _waking_admission()
    results: list[Any] = [_busy(), {"type": "inline"}]
    started_at = fake.clock
    polls = 0
    real_wait_condition = fake.wait_condition

    async def wait_condition(
        fn: Callable[[], bool], *, timeout: timedelta | None = None
    ) -> None:
        def poll() -> bool:
            nonlocal polls
            polls += 1
            if polls == 3:
                admission.wake()  # the executor's signal, mid-wait
            return fn()

        await real_wait_condition(poll, timeout=timeout)

    fake.wait_condition = wait_condition  # type: ignore[method-assign]

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        return results.pop(0)

    result = await admission.execute(dispatch, max_attempts=1)

    assert result == {"type": "inline"}
    assert polls >= 3
    assert fake.sleeps == []
    assert fake.clock == started_at  # the backoff never ran out


@pytest.mark.anyio
async def test_wake_before_the_busy_result_is_kept(fake: FakeWorkflow) -> None:
    """The release can wake the run before its busy result arrives."""
    admission = _waking_admission()
    results: list[Any] = [_busy(), {"type": "inline"}]
    waits = 0
    real_wait_condition = fake.wait_condition

    async def counting_wait_condition(
        fn: Callable[[], bool], *, timeout: timedelta | None = None
    ) -> None:
        nonlocal waits
        if timeout is not None:
            waits += 1
        await real_wait_condition(fn, timeout=timeout)

    fake.wait_condition = counting_wait_condition  # type: ignore[method-assign]

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        result = results.pop(0)
        if parse_not_admitted(result) is not None:
            admission.wake()  # lands while this dispatch is in flight
        return result

    result = await admission.execute(dispatch, max_attempts=1)

    assert result == {"type": "inline"}
    assert waits == 0  # no timer at all: the probe went at once
    assert fake.sleeps == []


@pytest.mark.anyio
async def test_timer_and_wake_together_give_one_probe(fake: FakeWorkflow) -> None:
    """A wake in the same activation as the timer is spent on that one probe."""
    admission = _waking_admission()
    results: list[Any] = [_busy(), _busy(), {"type": "inline"}]
    dispatches = 0
    timed_out = 0
    real_wait_condition = fake.wait_condition

    async def timer_fires_with_a_wake(
        fn: Callable[[], bool], *, timeout: timedelta | None = None
    ) -> None:
        nonlocal timed_out
        if timeout is None:  # a probe waiting for its admission signal
            return await real_wait_condition(fn)
        await asyncio.sleep(0)
        if timed_out == 0:
            admission.wake()
        timed_out += 1
        fake.clock += timeout
        raise TimeoutError

    fake.wait_condition = timer_fires_with_a_wake  # type: ignore[method-assign]

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        nonlocal dispatches
        dispatches += 1
        return results.pop(0)

    result = await admission.execute(dispatch, max_attempts=1)

    assert result == {"type": "inline"}
    assert dispatches == 3  # first dispatch + one probe per backoff
    assert timed_out == 2  # the second backoff waited its timer out


@pytest.mark.anyio
async def test_cancel_during_a_wake_wait_still_cancels(fake: FakeWorkflow) -> None:
    admission = _waking_admission()
    waiting_started = asyncio.Event()
    real_wait_condition = fake.wait_condition

    async def wait_condition(
        fn: Callable[[], bool], *, timeout: timedelta | None = None
    ) -> None:
        waiting_started.set()
        await asyncio.Event().wait()  # an unfired timer
        await real_wait_condition(fn, timeout=timeout)

    fake.wait_condition = wait_condition  # type: ignore[method-assign]

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        return _busy()

    task = asyncio.create_task(admission.execute(dispatch, max_attempts=1))
    await waiting_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    # Nothing waits any more, so a late wake is ignored.
    admission.wake()
    assert admission._woken is False  # pyright: ignore[reportPrivateUsage]


def test_wake_with_nothing_in_flight_is_ignored() -> None:
    admission = _waking_admission()

    admission.wake()

    assert admission._woken is False  # pyright: ignore[reportPrivateUsage]


@pytest.mark.anyio
async def test_admitted_probe_keeps_a_wake_for_a_dispatch_in_flight(
    fake: FakeWorkflow,
) -> None:
    """Another probe's admission must not spend a wake a busy dispatch still needs."""
    admission = _waking_admission()
    results: list[Any] = [_busy(), {"type": "inline"}]
    waits = 0
    real_wait_condition = fake.wait_condition

    async def counting_wait_condition(
        fn: Callable[[], bool], *, timeout: timedelta | None = None
    ) -> None:
        nonlocal waits
        if timeout is not None:
            waits += 1
        await real_wait_condition(fn, timeout=timeout)

    fake.wait_condition = counting_wait_condition  # type: ignore[method-assign]

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        result = results.pop(0)
        if parse_not_admitted(result) is not None:
            admission.wake()  # a release wakes the run ...
            admission._on_progress()  # pyright: ignore[reportPrivateUsage]
            # ... then another probe's admission lands, before this busy result.
        return result

    result = await admission.execute(dispatch, max_attempts=1)

    assert result == {"type": "inline"}
    assert waits == 0  # the wake survived: the probe went at once


@pytest.mark.anyio
async def test_without_the_wake_the_backoff_is_a_plain_timer(
    fake: FakeWorkflow,
) -> None:
    """Histories recorded before the wake keep ``workflow.sleep``; a wake is a no-op."""
    admission = _admission()
    results: list[Any] = [_busy(), {"type": "inline"}]

    async def dispatch(_max_attempts: int, _probe_id: str | None) -> Any:
        result = results.pop(0)
        admission.wake()
        return result

    await admission.execute(dispatch, max_attempts=1)

    assert len(fake.sleeps) == 1
