"""Gateway identity and drain logging for managed LiteLLM workers.

Each uvicorn worker of the managed LiteLLM service tags its responses with
``x-tracecat-gateway: <pod>/<pid>`` so the LLM socket proxy can attribute an
interrupted stream to the worker that served it.

On SIGTERM every worker logs drain start, done, and timeout with its in-flight
request count. The drain budget ``D`` is LiteLLM's ``GRACEFUL_SHUTDOWN_TIMEOUT``
(deployment env). Uvicorn 0.35 waits for open connections without a bound
before LiteLLM's own lifespan drain runs, so LiteLLM's drain always sees an
idle worker. This logger watches the in-flight counter from the signal instead,
so its timeout line marks streams still open after ``D``; those streams are
cut by the pod's SIGKILL at ``terminationGracePeriodSeconds``.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import threading
import time
from dataclasses import dataclass
from types import FrameType

from litellm.proxy.middleware.in_flight_requests_middleware import (
    get_in_flight_requests,
)
from litellm.proxy.shutdown.graceful_shutdown_manager import GracefulShutdownManager

from tracecat.logger import logger

GATEWAY_IDENTITY_HEADER = "x-tracecat-gateway"
_DRAIN_POLL_INTERVAL_SECONDS = 0.1

_drain_tasks: set[asyncio.Task[None]] = set()
_drain_logging_installed = False


def gateway_identity() -> str:
    """Return this worker's ``<pod>/<pid>`` identity.

    Kubernetes sets the container hostname to the pod name.
    """
    return f"{socket.gethostname()}/{os.getpid()}"


@dataclass(slots=True)
class _DrainLog:
    """One worker's drain, from SIGTERM to the end of its in-flight requests."""

    drain_timeout: float
    gateway: str
    started_at: float
    timed_out: bool = False
    finished: bool = False

    def finish(self) -> None:
        """Log the drain end once, from the poller or from app teardown."""
        if self.finished:
            return
        self.finished = True
        logger.info(
            "LLM gateway drain finished",
            gateway=self.gateway,
            in_flight_requests=get_in_flight_requests(),
            elapsed_seconds=round(time.monotonic() - self.started_at, 2),
            drain_timeout_exceeded=self.timed_out,
        )


_drain_log: _DrainLog | None = None


async def _watch_drain(drain: _DrainLog) -> None:
    """Poll this worker's in-flight requests until they finish."""
    logger.info(
        "LLM gateway drain started",
        gateway=drain.gateway,
        in_flight_requests=get_in_flight_requests(),
        drain_timeout_seconds=drain.drain_timeout,
    )
    while not drain.finished and (in_flight := get_in_flight_requests()) > 0:
        elapsed = time.monotonic() - drain.started_at
        if not drain.timed_out and elapsed >= drain.drain_timeout:
            drain.timed_out = True
            logger.warning(
                "LLM gateway drain timed out",
                gateway=drain.gateway,
                in_flight_requests=in_flight,
                elapsed_seconds=round(elapsed, 2),
                drain_timeout_seconds=drain.drain_timeout,
            )
        await asyncio.sleep(_DRAIN_POLL_INTERVAL_SECONDS)
    drain.finish()


def _finish_drain_log() -> None:
    if _drain_log is not None:
        _drain_log.finish()


def install_drain_logging() -> None:
    """Chain a drain logger in front of the running server's SIGTERM handler.

    LiteLLM imports the gateway module while uvicorn's worker is starting, after
    uvicorn installed its own SIGTERM handler, so the logger chains to it rather
    than replacing it. Outside a running worker (imports in tests or tooling)
    there is no callable handler or event loop, and this is a no-op.

    The poller can miss the last request: uvicorn exits right after app
    teardown by re-raising SIGTERM. LiteLLM's lifespan teardown calls
    ``GracefulShutdownManager.start_shutdown()`` once every connection has
    closed, so that call also finishes the drain log.
    """
    global _drain_logging_installed
    if _drain_logging_installed:
        return
    if threading.current_thread() is not threading.main_thread():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    previous = signal.getsignal(signal.SIGTERM)
    if not callable(previous):
        return

    def start_drain_log() -> None:
        global _drain_log
        if _drain_log is not None:
            return
        _drain_log = _DrainLog(
            drain_timeout=GracefulShutdownManager.get_timeout(),
            gateway=gateway_identity(),
            started_at=time.monotonic(),
        )
        task = loop.create_task(_watch_drain(_drain_log))
        _drain_tasks.add(task)

    def handle_sigterm(signum: int, frame: FrameType | None) -> None:
        # Signal handlers interrupt arbitrary code; only schedule work here.
        loop.call_soon_threadsafe(start_drain_log)
        previous(signum, frame)

    litellm_start_shutdown = GracefulShutdownManager.start_shutdown

    def start_shutdown() -> None:
        _finish_drain_log()
        litellm_start_shutdown()

    GracefulShutdownManager.start_shutdown = start_shutdown
    signal.signal(signal.SIGTERM, handle_sigterm)
    _drain_logging_installed = True
