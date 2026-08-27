"""Lifespan-owned supervision for API in-process background tasks.

The API server runs several asyncio tasks alongside request handling, e.g.
registry sync on startup, case trigger consumers, and case duration sync.
These tasks are not ASGI requests, so uvicorn's connection drain does not
cover them. This module owns that task lifecycle explicitly:

- Every supervised task is kept in a strong reference set (asyncio only keeps
  weak references, so unowned tasks can be garbage collected mid-flight).
- Once shutdown begins, no new tasks are spawned.
- On shutdown, long-running consumers are cancelled immediately (cancellation
  is their stop signal), and finite startup tasks are awaited for a bounded
  drain period before the deadline cancels whatever remains.

This protects in-process work during graceful shutdown (SIGTERM). Nothing
survives SIGKILL, OOM kills, or node loss — correctness-critical work must be
persisted in PostgreSQL, Temporal, or a durable queue so another pod can
resume it. The deployment's termination grace period must be longer than the
drain budget for the bounded await to have any effect.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any, Literal

from tracecat.logger import logger

type TaskKind = Literal["finite", "long_running"]


class LifespanTaskSupervisor:
    """Owns and drains the API lifespan's in-process background tasks.

    Each task is spawned with a kind:

    - ``"finite"``: startup work expected to complete, e.g. registry sync or
      catalog load. Drain awaits these up to ``drain_timeout`` seconds before
      cancelling the stragglers.
    - ``"long_running"``: consumers that run until shutdown, e.g. case
      triggers. Drain cancels these immediately; cancellation is their stop
      signal and they must clean up in response to it.

    The supervisor never resurrects or reschedules work. Tasks whose failure
    must not lose data should checkpoint to durable storage themselves.
    """

    def __init__(self, *, drain_timeout: float) -> None:
        self._tasks: dict[asyncio.Task[None], TaskKind] = {}
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

        Raises:
            RuntimeError: If called after drain has started.
        """
        if self._draining:
            raise RuntimeError(
                f"Refusing to spawn task {name!r}: the supervisor is draining"
            )
        task = asyncio.create_task(coro, name=name)
        self._tasks[task] = kind
        task.add_done_callback(self._on_task_done)
        logger.debug("Spawned supervised lifespan task", task=name, kind=kind)
        return task

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.pop(task, None)
        if task.cancelled():
            return
        # Surface unexpected failures of tasks that exit on their own while
        # the server is running; retrieve the exception so asyncio does not
        # log it as "Task exception was never retrieved".
        if (exc := task.exception()) is not None:
            logger.error(
                "Supervised lifespan task failed", task=task.get_name(), err=exc
            )

    async def drain(self) -> None:
        """Stop accepting tasks and drain supervised tasks in a bounded period.

        Phase 1 cancels long-running consumers so they stop accepting work.
        Phase 2 lets finite tasks finish within ``drain_timeout`` and cancels
        the stragglers. Phase 3 awaits every task's cancellation for the same
        budget so cleanup code runs before the process exits; tasks that
        ignore cancellation past the deadline are reported and abandoned to
        the process teardown.
        """
        self._draining = True
        if not self._tasks:
            return

        long_running = [
            task for task, kind in self._tasks.items() if kind == "long_running"
        ]
        finite = [task for task, kind in self._tasks.items() if kind == "finite"]

        # Phase 1: stop consumers immediately. They are expected to respond to
        # cancellation promptly.
        for task in long_running:
            task.cancel()

        # Phase 2: give finite tasks the full drain budget to complete.
        if finite:
            done, pending = await asyncio.wait(finite, timeout=self._drain_timeout)
            for task in pending:
                logger.warning(
                    "Lifespan task did not complete within the drain timeout; cancelling",
                    task=task.get_name(),
                    timeout=self._drain_timeout,
                )
                task.cancel()
            _log_completed_errors(done)

        # Phase 3: await cancellation-completion for everything, bounded.
        remaining = [task for task in self._tasks if not task.done()]
        if not remaining:
            return
        done, pending = await asyncio.wait(remaining, timeout=self._drain_timeout)
        _log_completed_errors(done)
        for task in pending:
            logger.warning(
                "Lifespan task did not shut down within the grace period",
                task=task.get_name(),
                timeout=self._drain_timeout,
            )


def _log_completed_errors(tasks: set[asyncio.Task[None]]) -> None:
    for task in tasks:
        if task.cancelled():
            continue
        if (exc := task.exception()) is not None:
            logger.warning(
                "Lifespan task stopped with error", task=task.get_name(), err=exc
            )
