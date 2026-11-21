"""Durable waits for work an executor answered busy.

Workflow code only: deterministic and Temporal-sandbox safe.

One ``RunAdmission`` serves one run and one resource. After a not-admitted answer the
run is *blocked*: one dispatch holds the probe token and waits on a durable
timer before dispatching again; the run's other dispatches wait for the token
instead of probing on their own timers. A probe that gets in hands the token to
the next waiter, which probes at once. A probe that is still busy restarts the
timer with a longer backoff. Because not admitting is the activity's result, the
workflow can't see admission directly: a probe carries ``ack_admission`` and the
executor signals the run when it admits it, so the token passes on then rather
than when the action finishes. A lost signal costs only time: the token also
passes on when the probe completes. When nobody is waiting, the run is unblocked.

There is no wait limit: accepted work never fails for capacity. Only the run's
own timeout or a cancellation ends a wait.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Final

from temporalio import workflow

from tracecat.admission.types import ConcurrencyResource, parse_not_admitted
from tracecat.dsl.workflow_logging import WorkflowRuntimeLogger

BACKOFF_BASE_SECONDS: Final = 2.0
BACKOFF_MAX_SECONDS: Final = 60.0
LONG_WAIT_SECONDS: Final = 15 * 60.0
"""A dispatch that has waited this long logs ``concurrency.long_wait`` once."""

type Dispatch = Callable[[int, str | None], Awaitable[Any]]
"""Dispatches one activity, given the user's remaining attempts (0 = unlimited) and,
for a probe that wants an admission signal, the activity ID to use."""


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
        self,
        *,
        resource: ConcurrencyResource,
        logger: WorkflowRuntimeLogger,
        acks_enabled: bool = False,
    ) -> None:
        self.resource = resource
        self.acks_enabled = acks_enabled
        """Whether probes ask for an admission signal (off for pre-patch histories)."""
        self._logger = logger
        self._in_flight_probes: set[str] = set()
        self._acked: set[str] = set()
        self._blocked = False
        self._token_held = False
        self._token_generation = 0
        self._waiters = 0
        self._needs_timer = False
        self._backoff_step = 0

    def _next_backoff(self) -> timedelta:
        exponential = BACKOFF_BASE_SECONDS * (2 ** min(self._backoff_step, 10))
        capped = min(exponential, BACKOFF_MAX_SECONDS)
        return timedelta(seconds=capped * workflow.random().uniform(0.8, 1.0))

    async def _take_token(self) -> int | None:
        """Wait until this dispatch holds the probe token or the run unblocks.

        Returns:
            The token's generation if it was taken, ``None`` if the run unblocked.
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
            return None
        self._token_held = True
        self._token_generation += 1
        return self._token_generation

    def _release_token(self, generation: int) -> None:
        """Release the token only if this holder still has it (not after a hand-off)."""
        if self._token_held and self._token_generation == generation:
            self._token_held = False

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

    @staticmethod
    async def _wait_backoff(
        delay: timedelta, interrupt: Callable[[], bool] | None
    ) -> bool:
        """Wait out a backoff on a durable timer, or until ``interrupt`` is true.

        Returns:
            Whether ``interrupt`` ended the wait early.
        """
        if interrupt is None:
            await workflow.sleep(delay)
            return False
        try:
            await workflow.wait_condition(interrupt, timeout=delay)
        except TimeoutError:
            return False
        return True

    def acknowledge(self, activity_id: str) -> None:
        """Record an executor's admission signal for a probe still in flight."""
        if activity_id in self._in_flight_probes:
            self._acked.add(activity_id)

    async def _probe(
        self, dispatch: Dispatch, remaining: int, generation: int
    ) -> tuple[Any, bool]:
        """Dispatch while holding the token; hand it on once the probe is admitted.

        Returns:
            The result, and whether the probe's admission was already recorded
            (an admission signal arrived before the result).
        """
        if not self.acks_enabled:
            probe_id = None
            pending = asyncio.ensure_future(dispatch(remaining, None))
        else:
            probe_id = f"admission-probe-{workflow.uuid4()}"
            self._in_flight_probes.add(probe_id)
            pending = asyncio.ensure_future(dispatch(remaining, probe_id))
        acked = False
        try:
            if probe_id is not None:
                await workflow.wait_condition(
                    lambda: pending.done() or probe_id in self._acked
                )
                acked = probe_id in self._acked and not pending.done()
                if acked:
                    # Admitted and running: let the next waiter probe now.
                    self._on_progress()
                    self._release_token(generation)
            return await pending, acked
        except Exception:
            # Busy is a result, never an error: a failed probe was admitted.
            if not acked:
                self._on_progress()
            raise
        except BaseException:
            pending.cancel()
            raise
        finally:
            if probe_id is not None:
                self._in_flight_probes.discard(probe_id)
                self._acked.discard(probe_id)

    async def execute(
        self,
        dispatch: Dispatch,
        *,
        max_attempts: int,
        interrupt: Callable[[], bool] | None = None,
    ) -> Any:
        """Dispatch until an attempt is admitted; return its result.

        Only probes change the run's shared wait state, and each probe records
        its admission once. A dispatch sent before the run was blocked never
        unblocks it: its completion says nothing about newer not-admitted answers.

        Args:
            dispatch: Starts the activity with the remaining user attempts.
            max_attempts: The user's attempt budget (0 = unlimited).
            interrupt: Ends a backoff early when it returns true, so the next
                probe dispatches at once (for example on a user cancel, which
                the dispatch then handles). It ends one backoff per call; later
                backoffs are plain timers, so a dispatch that ignores it can't
                spin. ``None`` keeps a plain timer.

        Returns:
            The first result that isn't a not-admitted answer.

        Raises:
            Whatever ``dispatch`` raises for an admitted attempt.
        """
        remaining = max_attempts
        waited_since: datetime | None = None
        bounces = 0
        long_wait_logged = False
        interrupted = False

        while True:
            token: int | None = None
            if self._blocked:
                waited_since = waited_since or workflow.now()
                token = await self._take_token()
            # True when this result must not change the shared wait state.
            progress_recorded = True
            try:
                if token is not None:
                    if self._needs_timer:
                        delay = self._next_backoff()
                        self._backoff_step += 1
                        interrupted = (
                            await self._wait_backoff(
                                delay, None if interrupted else interrupt
                            )
                            or interrupted
                        )
                    result, progress_recorded = await self._probe(
                        dispatch, remaining, token
                    )
                else:
                    result = await dispatch(remaining, None)
            finally:
                if token is not None:
                    self._release_token(token)
            not_admitted = parse_not_admitted(result)
            if not_admitted is None:
                if not progress_recorded:
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
            remaining = remaining_attempts_after_busy(remaining, not_admitted.attempt)
            waited_since = waited_since or workflow.now()
            waited = (workflow.now() - waited_since).total_seconds()
            if not long_wait_logged and waited >= LONG_WAIT_SECONDS:
                long_wait_logged = True
                self._logger.warning(
                    "concurrency.long_wait",
                    resource=self.resource.value,
                    in_use=not_admitted.in_use,
                    limit=not_admitted.limit,
                    wait_seconds=waited,
                    bounces=bounces,
                )
