"""Event-loop lag sampler for worker processes.

One background task sleeps for a 100 ms tick and records how late it woke up.
``max_lag_seconds()`` is the largest lag over the last ~2 s, including a tick
that is overdue right now, so a stalled loop shows up before the sampler runs
again. The same value is exported as the ``process.event_loop.lag`` gauge.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable
from typing import Final

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation

TICK_SECONDS: Final = 0.1
WINDOW_SECONDS: Final = 2.0


class LoopLagSampler:
    """Measures how late a periodic sleep wakes up on the running loop."""

    def __init__(
        self,
        *,
        tick_seconds: float = TICK_SECONDS,
        window_seconds: float = WINDOW_SECONDS,
    ) -> None:
        self._tick = tick_seconds
        self._window = window_seconds
        self._samples: deque[tuple[float, float]] = deque()
        """(loop time of the sample, lag in seconds), oldest first."""
        self._last_tick: float | None = None
        self._window_max = 0.0
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start sampling on the running loop; a no-op if already running there."""
        loop = asyncio.get_running_loop()
        if (
            self._task is not None
            and not self._task.done()
            and self._task.get_loop() is loop
        ):
            return
        self._samples.clear()
        self._window_max = 0.0
        self._last_tick = loop.time()
        self._task = loop.create_task(self._run(), name="loop-lag-sampler")

    async def stop(self) -> None:
        """Stop sampling."""
        if (task := self._task) is None:
            return
        self._task = None
        self._last_tick = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            expected = loop.time() + self._tick
            await asyncio.sleep(self._tick)
            now = loop.time()
            self._last_tick = now
            self._samples.append((now, max(0.0, now - expected)))
            while self._samples and self._samples[0][0] < now - self._window:
                self._samples.popleft()
            self._window_max = max(lag for _, lag in self._samples)

    def max_lag_seconds(self) -> float | None:
        """Largest lag over the window, or ``None`` when not sampling on this loop."""
        task = self._task
        if task is None or task.done() or self._last_tick is None:
            return None
        loop = asyncio.get_running_loop()
        if task.get_loop() is not loop:
            return None
        overdue = loop.time() - self._last_tick - self._tick
        return max(self._window_max, overdue, 0.0)

    def _observe(self, _options: CallbackOptions) -> Iterable[Observation]:
        # Runs on the exporter thread: read the float the loop last wrote.
        if self._task is None:
            return []
        return [Observation(self._window_max)]


sampler: Final = LoopLagSampler()
"""The process sampler; workers start it, the admission gate reads it."""

metrics.get_meter("tracecat.process").create_observable_gauge(
    "process.event_loop.lag",
    callbacks=[sampler._observe],
    unit="s",
    description="Largest asyncio event-loop lag over the last ~2 s.",
)
