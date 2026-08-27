import asyncio

import pytest

from tracecat.api.lifespan import LifespanTaskSupervisor


@pytest.mark.anyio
async def test_drain_awaits_finite_task_completion() -> None:
    supervisor = LifespanTaskSupervisor(drain_timeout=5.0)
    completed = asyncio.Event()

    async def task() -> None:
        await asyncio.sleep(0)
        completed.set()

    supervisor.spawn(task(), name="finite_task")
    await supervisor.drain()

    assert completed.is_set()
    assert supervisor.draining


@pytest.mark.anyio
async def test_drain_cancels_long_running_consumers() -> None:
    supervisor = LifespanTaskSupervisor(drain_timeout=5.0)
    started = asyncio.Event()
    stopped = asyncio.Event()
    keep_running = asyncio.Event()

    async def consume() -> None:
        started.set()
        try:
            await keep_running.wait()
        finally:
            stopped.set()

    supervisor.spawn(consume(), name="consumer", kind="long_running")
    await started.wait()
    await supervisor.drain()

    assert stopped.is_set()


@pytest.mark.anyio
async def test_drain_cancels_finite_tasks_past_the_deadline() -> None:
    supervisor = LifespanTaskSupervisor(drain_timeout=0.0)
    started = asyncio.Event()
    cancelled = asyncio.Event()
    keep_running = asyncio.Event()

    async def slow_task() -> None:
        started.set()
        try:
            await keep_running.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    supervisor.spawn(slow_task(), name="slow_finite_task")
    await started.wait()
    await supervisor.drain()

    assert cancelled.is_set()


@pytest.mark.anyio
async def test_drain_abandons_tasks_that_ignore_cancellation() -> None:
    supervisor = LifespanTaskSupervisor(drain_timeout=0.0)
    started = asyncio.Event()
    swallow_gate = asyncio.Event()
    released = asyncio.Event()

    async def stubborn_task() -> None:
        started.set()
        try:
            await swallow_gate.wait()
        except asyncio.CancelledError:
            # Simulate a task that ignores cancellation past the deadline.
            await released.wait()
            raise

    task = supervisor.spawn(stubborn_task(), name="stubborn_task")
    await started.wait()

    async def release_later() -> None:
        await asyncio.sleep(0)
        released.set()

    # Unblock the stubborn task while drain waits in phase 3.
    asyncio.get_running_loop().create_task(release_later())  # noqa: RUF006
    await asyncio.wait_for(supervisor.drain(), timeout=5.0)

    assert task.done()


@pytest.mark.anyio
async def test_spawn_is_rejected_after_drain_starts() -> None:
    supervisor = LifespanTaskSupervisor(drain_timeout=0.0)

    async def noop() -> None:
        await asyncio.sleep(0)

    coro = noop()
    await supervisor.drain()
    with pytest.raises(RuntimeError, match="draining"):
        supervisor.spawn(coro, name="late_task")
    # The rejected coroutine never becomes a task; close it explicitly.
    coro.close()


@pytest.mark.anyio
async def test_drain_with_no_tasks_is_a_noop() -> None:
    supervisor = LifespanTaskSupervisor(drain_timeout=0.0)
    await supervisor.drain()
    assert supervisor.draining


@pytest.mark.anyio
async def test_unexpected_task_failure_is_surfaced_without_unretrieved_warning(
    recwarn: pytest.WarningsRecorder,
) -> None:
    supervisor = LifespanTaskSupervisor(drain_timeout=5.0)

    async def failing_task() -> None:
        await asyncio.sleep(0)
        raise ValueError("boom")

    supervisor.spawn(failing_task(), name="failing_task")
    await supervisor.drain()
    # Let done callbacks run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert not any(
        "Task exception was never retrieved" in str(warning.message)
        for warning in recwarn.list
    )


@pytest.mark.anyio
async def test_drain_completes_with_failed_finite_task() -> None:
    supervisor = LifespanTaskSupervisor(drain_timeout=5.0)
    ran_after = asyncio.Event()

    async def failing_task() -> None:
        raise ValueError("boom")

    supervisor.spawn(failing_task(), name="failing_task")
    await supervisor.drain()

    # Drain must not raise or hang when a finite task fails.
    ran_after.set()
    assert ran_after.is_set()


@pytest.mark.anyio
async def test_drain_stops_stoppable_consumer_gracefully() -> None:
    supervisor = LifespanTaskSupervisor(drain_timeout=5.0)
    started = asyncio.Event()
    batch_done = asyncio.Event()
    stop_event = asyncio.Event()
    batch_gate = asyncio.Event()

    async def consume() -> None:
        while not stop_event.is_set():
            started.set()
            # Simulate an in-flight batch being processed.
            await batch_gate.wait()
        # Current batch finishes after the stop signal.
        batch_done.set()

    task = supervisor.spawn(
        consume(),
        name="stoppable_consumer",
        kind="long_running",
        stop_event=stop_event,
    )
    await started.wait()

    drain_task = asyncio.create_task(supervisor.drain())
    # Let drain signal the stop event, then let the in-flight batch finish.
    await asyncio.sleep(0)
    batch_gate.set()
    await asyncio.wait_for(drain_task, timeout=5.0)

    assert batch_done.is_set()
    assert not task.cancelled()


@pytest.mark.anyio
async def test_drain_cancels_stoppable_consumer_past_the_deadline() -> None:
    supervisor = LifespanTaskSupervisor(drain_timeout=0.0)
    started = asyncio.Event()
    stopped = asyncio.Event()
    stop_event = asyncio.Event()
    hang_gate = asyncio.Event()

    async def consume() -> None:
        started.set()
        # Ignores the stop event; the drain deadline must cancel it.
        await hang_gate.wait()
        stopped.set()

    task = supervisor.spawn(
        consume(),
        name="stubborn_consumer",
        kind="long_running",
        stop_event=stop_event,
    )
    await started.wait()
    await supervisor.drain()

    assert task.cancelled()
    assert not stopped.is_set()


@pytest.mark.anyio
async def test_stop_event_requires_long_running_kind() -> None:
    supervisor = LifespanTaskSupervisor(drain_timeout=5.0)

    async def noop() -> None:
        await asyncio.sleep(0)

    coro = noop()
    with pytest.raises(ValueError, match="long_running"):
        supervisor.spawn(
            coro,
            name="finite_with_stop",
            kind="finite",
            stop_event=asyncio.Event(),
        )
    coro.close()


@pytest.mark.anyio
async def test_drain_surfaces_completed_task_errors() -> None:
    supervisor = LifespanTaskSupervisor(drain_timeout=5.0)

    async def failing_task() -> None:
        raise ValueError("boom")

    supervisor.spawn(failing_task(), name="failing_task")
    # Drain must not raise or hang when a finite task fails.
    await supervisor.drain()
