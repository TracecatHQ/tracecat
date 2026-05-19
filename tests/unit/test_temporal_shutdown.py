from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable

import pytest

from tracecat.temporal.worker_lifecycle import (
    install_worker_shutdown_signal_handlers,
    run_worker_entrypoint,
)


class FakeSignalHandlingLoop:
    def __init__(self) -> None:
        self.handlers: dict[
            signal.Signals, tuple[Callable[..., object], tuple[object, ...]]
        ] = {}
        self.removed: list[signal.Signals] = []

    def add_signal_handler(
        self, sig: int, callback: Callable[..., object], *args: object
    ) -> None:
        self.handlers[signal.Signals(sig)] = (callback, args)

    def remove_signal_handler(self, sig: int) -> bool:
        self.removed.append(signal.Signals(sig))
        return True


@pytest.mark.anyio
async def test_worker_shutdown_signal_handler_wakes_event_from_loop_callback() -> None:
    shutdown_event = asyncio.Event()
    loop = FakeSignalHandlingLoop()

    with install_worker_shutdown_signal_handlers(shutdown_event, loop=loop):
        callback, args = loop.handlers[signal.SIGTERM]
        callback(*args)

        assert shutdown_event.is_set()

    assert loop.removed == [signal.SIGINT, signal.SIGTERM]


@pytest.mark.anyio
async def test_worker_shutdown_signal_handler_respects_custom_signals() -> None:
    shutdown_event = asyncio.Event()
    loop = FakeSignalHandlingLoop()

    with install_worker_shutdown_signal_handlers(
        shutdown_event, loop=loop, signals=(signal.SIGTERM,)
    ):
        assert list(loop.handlers) == [signal.SIGTERM]

    assert loop.removed == [signal.SIGTERM]


def test_run_worker_entrypoint_runs_main_with_shutdown_event() -> None:
    called = False

    async def main(shutdown_event: asyncio.Event) -> None:
        nonlocal called
        called = True
        shutdown_event.set()

    run_worker_entrypoint(main)

    assert called
