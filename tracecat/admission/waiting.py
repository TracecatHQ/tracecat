"""Durable waits for work an executor answered busy.

Workflow code only: deterministic and Temporal-sandbox safe.

One ``RunAdmission`` serves one run and one resource. After a busy answer the
run is *blocked*: one dispatch holds the probe token and waits on a durable
timer before dispatching again; the run's other dispatches wait for the token
instead of probing on their own timers. A probe that gets in hands the token to
the next waiter, which probes at once. A probe that is still busy restarts the
timer with a longer backoff. When nobody is waiting, the run is unblocked.

There is no wait limit: accepted work never fails for capacity. Only the run's
own timeout or a cancellation ends a wait.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Final

from temporalio import workflow

from tracecat.admission.types import ConcurrencyResource, parse_busy_result
from tracecat.dsl.workflow_logging import WorkflowRuntimeLogger

BACKOFF_BASE_SECONDS: Final = 2.0
BACKOFF_MAX_SECONDS: Final = 60.0
LONG_WAIT_SECONDS: Final = 15 * 60.0
"""A dispatch that has waited this long logs ``concurrency.long_wait`` once."""

type Dispatch = Callable[[int], Awaitable[Any]]
"""Dispatches one activity, given the user's remaining attempts (0 = unlimited)."""


def remaining_attempts_after_busy(remaining: int, busy_attempt: int) -> int:
    """User attempts left after an activity came back busy on ``busy_attempt``.

    Attempts before the busy one executed and count; the busy one executed
    nothing and doesn't.
    """
    if remaining == 0:
        return 0
    return max(1, remaining - (busy_attempt - 1))


class RunAdmission:
    """Per-run wait state for one resource."""

    def __init__(
        self, *, resource: ConcurrencyResource, logger: WorkflowRuntimeLogger
    ) -> None:
        self.resource = resource
        self._logger = logger
        self._blocked = False
        self._token_held = False
        self._waiters = 0
        self._needs_timer = False
        self._backoff_step = 0

    def _next_backoff(self) -> timedelta:
        exponential = BACKOFF_BASE_SECONDS * (2 ** min(self._backoff_step, 10))
        capped = min(exponential, BACKOFF_MAX_SECONDS)
        return timedelta(seconds=capped * workflow.random().uniform(0.8, 1.0))

    async def _take_token(self) -> bool:
        """Wait until this dispatch holds the probe token or the run unblocks.

        Returns:
            ``True`` if the token was taken, ``False`` if the run unblocked.
        """
        self._waiters += 1
        try:
            # Several waiters can wake in one pass; re-check after each wake.
            while self._blocked and self._token_held:
                await workflow.wait_condition(
                    lambda: not self._blocked or not self._token_held
                )
        finally:
            self._waiters -= 1
        if not self._blocked:
            return False
        self._token_held = True
        return True

    def _on_progress(self) -> None:
        """An attempt was admitted: the next waiter probes without a timer."""
        self._needs_timer = False
        self._backoff_step = 0
        if self._waiters == 0:
            self._blocked = False

    def _on_busy(self) -> None:
        """An attempt came back busy: the next probe waits on a timer."""
        if not self._blocked:
            self._blocked = True
            self._backoff_step = 0
        self._needs_timer = True

    async def execute(self, dispatch: Dispatch, *, max_attempts: int) -> Any:
        """Dispatch until an attempt is admitted; return its result.

        Args:
            dispatch: Starts the activity with the remaining user attempts.
            max_attempts: The user's attempt budget (0 = unlimited).

        Returns:
            The first result that isn't a busy answer.

        Raises:
            Whatever ``dispatch`` raises for an admitted attempt.
        """
        remaining = max_attempts
        waited_since: datetime | None = None
        bounces = 0
        long_wait_logged = False

        while True:
            probing = False
            if self._blocked:
                waited_since = waited_since or workflow.now()
                probing = await self._take_token()
            try:
                if probing and self._needs_timer:
                    delay = self._next_backoff()
                    self._backoff_step += 1
                    await workflow.sleep(delay)
                result = await dispatch(remaining)
            except Exception:
                # Busy is a result, never an error: a failed attempt was admitted.
                self._on_progress()
                raise
            finally:
                if probing:
                    self._token_held = False
            busy = parse_busy_result(result)
            if busy is None:
                self._on_progress()
                if waited_since is not None:
                    self._logger.info(
                        "concurrency.admitted_after_wait",
                        resource=self.resource.value,
                        wait_seconds=(workflow.now() - waited_since).total_seconds(),
                        bounces=bounces,
                    )
                return result

            self._on_busy()
            bounces += 1
            remaining = remaining_attempts_after_busy(remaining, busy.attempt)
            waited_since = waited_since or workflow.now()
            waited = (workflow.now() - waited_since).total_seconds()
            if not long_wait_logged and waited >= LONG_WAIT_SECONDS:
                long_wait_logged = True
                self._logger.warning(
                    "concurrency.long_wait",
                    resource=self.resource.value,
                    in_use=busy.in_use,
                    limit=busy.limit,
                    wait_seconds=waited,
                    bounces=bounces,
                )
