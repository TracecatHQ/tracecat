"""Lifespan-owned supervision for API in-process background tasks.

The API server runs several asyncio tasks alongside request handling, e.g.
registry sync on startup, case trigger consumers, and case duration sync.
These tasks are not ASGI requests, so uvicorn's connection drain does not
cover them. This module owns that task lifecycle explicitly:

- Every supervised task is kept in a strong reference set (asyncio only keeps
  weak references, so unowned tasks can be garbage collected mid-flight).
- Once shutdown begins, no new tasks are spawned.
- On shutdown, stoppable consumers (case triggers, case duration sync) are
  signalled via a stop event so they finish their in-flight batch — including
  Redis stream acks — and exit cleanly; this prevents rolling upgrades from
  stranding unacked events behind the terminating pod. Consumers without a
  stop event, and any stragglers past the drain deadline, are cancelled.

This protects in-process work during graceful shutdown (SIGTERM). Nothing
survives SIGKILL, OOM kills, or node loss — correctness-critical work must be
persisted in PostgreSQL, Temporal, or a durable queue so another pod can
resume it. The deployment's termination grace period must be longer than the
drain budget for the bounded await to have any effect.
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Literal

from tracecat.logger import logger
from tracecat.observability.sentry import capture_api_background_task_failure

type TaskKind = Literal["finite", "long_running"]
type StoppableTaskFactory = Callable[[asyncio.Event], Coroutine[Any, Any, None]]


class LifespanTaskSupervisor:
    """Owns and drains the API lifespan's in-process background tasks.

    Each task is spawned with a kind:

    - ``"finite"``: startup work expected to complete, e.g. registry sync or
      catalog load. Drain awaits these up to ``drain_timeout`` seconds before
      cancelling the stragglers.
    - ``"long_running"``: consumers that run until shutdown, e.g. case
      triggers. Drain cancels these immediately; cancellation is their stop
      signal and they must clean up in response to it.

    ``spawn_stoppable()`` creates a stop event and injects it into a
    long-running task factory atomically. On shutdown the supervisor sets the
    event and waits (bounded by ``drain_timeout``) for the task to finish its
    in-flight work and exit on its own, cancelling it only if it exceeds the
    deadline. This is the preferred shutdown path for stream consumers: the
    last batch is fully processed and acked instead of being redelivered to
    another pod after the idle-claim window.

    The supervisor never resurrects or reschedules work. Tasks whose failure
    must not lose data should checkpoint to durable storage themselves.
    """

    def __init__(self, *, drain_timeout: float) -> None:
        self._tasks: dict[asyncio.Task[None], TaskKind] = {}
        self._stop_events: dict[asyncio.Task[None], asyncio.Event] = {}
        self._drain_timeout = drain_timeout
        self._draining = False

    @property
    def draining(self) -> bool:
        """Whether shutdown has started and new spawns are rejected."""
        return self._draining

    def spawn(
        self,
        coro: Coroutine[Any, Any, None],
        *,
        name: str,
        kind: TaskKind = "finite",
    ) -> asyncio.Task[None]:
        """Create a task under supervision.

        Args:
            coro: Coroutine to run as a supervised task.
            name: Unique name used for log lines and cancellation reporting.
            kind: Whether the task is expected to finish ("finite") or run
                until shutdown ("long_running").

        Raises:
            RuntimeError: If called after drain has started.
        """
        self._raise_if_draining(name)
        return self._create_task(coro, name=name, kind=kind)

    def spawn_stoppable(
        self,
        factory: StoppableTaskFactory,
        *,
        name: str,
    ) -> asyncio.Task[None]:
        """Create a stoppable long-running task under supervision.

        The supervisor creates the stop event and passes the exact same object
        to the task factory and its shutdown registry, so callers cannot wire
        the consumer to a different event than the one drain signals.

        Args:
            factory: Callable that accepts the supervisor-owned stop event and
                returns the consumer coroutine.
            name: Unique name used for log lines and cancellation reporting.

        Raises:
            RuntimeError: If called after drain has started. The factory is not
                invoked when registration is rejected.
        """
        self._raise_if_draining(name)
        stop_event = asyncio.Event()
        return self._create_task(
            factory(stop_event),
            name=name,
            kind="long_running",
            stop_event=stop_event,
        )

    def _raise_if_draining(self, name: str) -> None:
        if self._draining:
            raise RuntimeError(
                f"Refusing to spawn task {name!r}: the supervisor is draining"
            )

    def _create_task(
        self,
        coro: Coroutine[Any, Any, None],
        *,
        name: str,
        kind: TaskKind,
        stop_event: asyncio.Event | None = None,
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(coro, name=name)
        self._tasks[task] = kind
        if stop_event is not None:
            self._stop_events[task] = stop_event
        task.add_done_callback(self._on_task_done)
        logger.debug("Spawned supervised lifespan task", task=name, kind=kind)
        return task

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.pop(task, None)
        self._stop_events.pop(task, None)
        if task.cancelled():
            return
        # Surface unexpected failures of tasks that exit on their own while
        # the server is running; retrieve the exception so asyncio does not
        # log it as "Task exception was never retrieved".
        if (exc := task.exception()) is not None:
            capture_api_background_task_failure(exc, task_name=task.get_name())
            logger.error(
                "Supervised lifespan task failed",
                task=task.get_name(),
                err=exc,
            )

    async def drain(self) -> None:
        """Stop accepting tasks and drain supervised tasks in a bounded period.

        Phase 1 signals stoppable consumers (they finish their in-flight
        batch and exit) and immediately cancels consumers without a stop
        event. Phase 2 lets finite tasks and stoppable consumers finish
        within ``drain_timeout`` and cancels the stragglers. Phase 3 awaits
        every task's cancellation for the same budget so cleanup code runs
        before the process exits; tasks that ignore cancellation past the
        deadline are reported and abandoned to the process teardown.
        """
        self._draining = True
        if not self._tasks:
            return

        stoppable: list[asyncio.Task[None]] = []
        finite: list[asyncio.Task[None]] = []
        for task, kind in self._tasks.items():
            if kind == "long_running":
                stop_event = self._stop_events.get(task)
                if stop_event is not None:
                    stop_event.set()
                    stoppable.append(task)
                else:
                    # Cancellation is the only stop signal for consumers
                    # without a stop event.
                    task.cancel()
            else:
                finite.append(task)

        # Phase 2: give tasks that should exit on their own the full drain
        # budget to complete.
        draining = finite + stoppable
        if draining:
            _, pending = await asyncio.wait(draining, timeout=self._drain_timeout)
            for task in pending:
                logger.warning(
                    "Lifespan task did not complete within the drain timeout; cancelling",
                    task=task.get_name(),
                    timeout=self._drain_timeout,
                )
                task.cancel()

        # Phase 3: await cancellation-completion for everything, bounded.
        remaining = [task for task in self._tasks if not task.done()]
        if not remaining:
            return
        _, pending = await asyncio.wait(remaining, timeout=self._drain_timeout)
        for task in pending:
            logger.warning(
                "Lifespan task did not shut down within the grace period",
                task=task.get_name(),
                timeout=self._drain_timeout,
            )
