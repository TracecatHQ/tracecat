"""Gateway worker identity header and SIGTERM drain logging."""

import asyncio
import os
import signal
import time
from unittest.mock import Mock

import litellm
import pytest
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.common_utils.user_api_key_cache import UserApiKeyCache
from litellm.proxy.shutdown.graceful_shutdown_manager import GracefulShutdownManager
from litellm.proxy.utils import ProxyLogging

from tracecat.agent import gateway_drain
from tracecat.agent.gateway import TracecatCallbackHandler


@pytest.mark.anyio
async def test_response_headers_carry_gateway_worker_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LiteLLM's own header merge adds ``x-tracecat-gateway: <pod>/<pid>``."""
    monkeypatch.setattr(litellm, "callbacks", [TracecatCallbackHandler()])
    monkeypatch.setattr(gateway_drain.socket, "gethostname", lambda: "litellm-0")
    proxy_logging = ProxyLogging(user_api_key_cache=UserApiKeyCache())

    headers = await proxy_logging.post_call_response_headers_hook(
        data={}, user_api_key_dict=UserAPIKeyAuth(), response=None
    )

    assert headers == {"x-tracecat-gateway": f"litellm-0/{os.getpid()}"}


@pytest.mark.anyio
@pytest.mark.parametrize("timed_out", [True, False])
async def test_drain_log_reports_in_flight_requests_against_budget(
    monkeypatch: pytest.MonkeyPatch,
    timed_out: bool,
) -> None:
    """Drain logs start, a single timeout past ``D``, and finishes at teardown.

    Zero in flight does not finish the log: uvicorn can still accept a request
    until its listener closes, so only teardown ends the drain.
    """
    in_flight = 2
    monkeypatch.setattr(gateway_drain, "get_in_flight_requests", lambda: in_flight)
    monkeypatch.setattr(gateway_drain, "_DRAIN_POLL_INTERVAL_SECONDS", 0.001)
    log = Mock()
    monkeypatch.setattr(gateway_drain, "logger", log)

    drain = gateway_drain._DrainLog(
        drain_timeout=0.0 if timed_out else 3600.0,
        gateway="litellm-0/1",
        started_at=time.monotonic(),
    )
    poller = asyncio.create_task(gateway_drain._watch_drain(drain))
    await asyncio.sleep(0.02)
    in_flight = 0
    await asyncio.sleep(0.02)

    assert [call.args[0] for call in log.info.call_args_list] == [
        "LLM gateway drain started"
    ]
    started = log.info.call_args
    assert started.kwargs["in_flight_requests"] == 2
    assert started.kwargs["gateway"] == "litellm-0/1"
    if timed_out:
        assert log.warning.call_count == 1
        timeout_log = log.warning.call_args
        assert timeout_log.args == ("LLM gateway drain timed out",)
        assert timeout_log.kwargs["in_flight_requests"] == 2
    else:
        log.warning.assert_not_called()

    drain.finish()
    await asyncio.wait_for(poller, timeout=1)

    finished = log.info.call_args
    assert finished.args == ("LLM gateway drain finished",)
    assert finished.kwargs["in_flight_requests"] == 0
    assert finished.kwargs["drain_timeout_exceeded"] is timed_out
    assert log.info.call_count == 2


@pytest.fixture
def fresh_drain_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset module state; monkeypatch restores LiteLLM's teardown hook."""
    monkeypatch.setattr(gateway_drain, "_drain_tasks", set())
    monkeypatch.setattr(gateway_drain, "_drain_log", None)
    monkeypatch.setattr(gateway_drain, "_drain_logging_installed", False)
    monkeypatch.setattr(
        GracefulShutdownManager,
        "start_shutdown",
        GracefulShutdownManager.start_shutdown,
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("fresh_drain_logging")
async def test_sigterm_starts_one_drain_log_and_keeps_server_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGTERM reaches uvicorn's handler and starts the drain log once."""
    server_handler = Mock()
    drain_timeouts: list[float] = []
    drain_started = asyncio.Event()

    async def fake_watch_drain(drain: gateway_drain._DrainLog) -> None:
        drain_timeouts.append(drain.drain_timeout)
        drain_started.set()

    monkeypatch.setattr(gateway_drain, "_watch_drain", fake_watch_drain)
    monkeypatch.setenv("GRACEFUL_SHUTDOWN_TIMEOUT", "120")
    original = signal.signal(signal.SIGTERM, server_handler)
    try:
        gateway_drain.install_drain_logging()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        assert handler is not server_handler

        handler(signal.SIGTERM, None)
        handler(signal.SIGTERM, None)
        await asyncio.wait_for(drain_started.wait(), timeout=1)
        await asyncio.sleep(0)
    finally:
        signal.signal(signal.SIGTERM, original)

    assert server_handler.call_count == 2
    assert drain_timeouts == [120.0]


@pytest.mark.anyio
@pytest.mark.usefixtures("fresh_drain_logging")
async def test_app_teardown_finishes_drain_log_before_poller_wakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uvicorn exits right after teardown, so teardown must log the finish."""
    in_flight = 1
    monkeypatch.setattr(gateway_drain, "get_in_flight_requests", lambda: in_flight)
    monkeypatch.setattr(gateway_drain, "_DRAIN_POLL_INTERVAL_SECONDS", 3600.0)
    log = Mock()
    monkeypatch.setattr(gateway_drain, "logger", log)
    original = signal.signal(signal.SIGTERM, Mock())
    try:
        gateway_drain.install_drain_logging()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        await asyncio.sleep(0.01)
        assert [call.args[0] for call in log.info.call_args_list] == [
            "LLM gateway drain started"
        ]

        # The last request ends while the poller sleeps; LiteLLM's lifespan
        # teardown then calls start_shutdown before the worker exits.
        in_flight = 0
        GracefulShutdownManager.start_shutdown()
    finally:
        signal.signal(signal.SIGTERM, original)
        GracefulShutdownManager.reset()

    finished = log.info.call_args_list[-1]
    assert finished.args == ("LLM gateway drain finished",)
    assert finished.kwargs["in_flight_requests"] == 0
    assert finished.kwargs["drain_timeout_exceeded"] is False
    for task in gateway_drain._drain_tasks:
        task.cancel()


def test_drain_logging_is_noop_outside_a_running_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing the gateway in tests or tooling leaves SIGTERM untouched."""
    monkeypatch.setattr(gateway_drain, "_drain_logging_installed", False)
    before = signal.getsignal(signal.SIGTERM)

    gateway_drain.install_drain_logging()

    assert signal.getsignal(signal.SIGTERM) is before
    assert gateway_drain._drain_logging_installed is False
