from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Protocol

from tracecat.logger import logger

WORKER_SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class SignalHandlingLoop(Protocol):
    def add_signal_handler(
        self, sig: int, callback: Callable[..., object], *args: object
    ) -> None: ...

    def remove_signal_handler(self, sig: int) -> bool: ...


@contextmanager
def install_worker_shutdown_signal_handlers(
    shutdown_event: asyncio.Event,
    *,
    loop: SignalHandlingLoop | None = None,
    signals: Sequence[signal.Signals] = WORKER_SHUTDOWN_SIGNALS,
) -> Iterator[None]:
    """Wake a worker shutdown event from the running event loop's signal path."""
    event_loop = loop or asyncio.get_running_loop()
    installed: list[signal.Signals] = []

    def request_shutdown(sig: signal.Signals) -> None:
        logger.info("Received shutdown signal", signal=sig.name)
        shutdown_event.set()

    try:
        for sig in signals:
            event_loop.add_signal_handler(sig, request_shutdown, sig)
            installed.append(sig)
        yield
    finally:
        for sig in installed:
            event_loop.remove_signal_handler(sig)
