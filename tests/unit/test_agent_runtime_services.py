from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.agent import runtime_services
from tracecat.agent.runtime_services import LiteLLMProxyStatus


@pytest.mark.anyio
async def test_wait_for_socket_returns_false_when_socket_never_appears(
    tmp_path: Path,
) -> None:
    assert (
        await runtime_services._wait_for_socket(
            tmp_path / "missing.sock",
            attempts=2,
            interval=0,
        )
        is False
    )


@pytest.mark.anyio
async def test_start_mcp_server_raises_when_socket_is_not_created(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "trusted-mcp.sock"

    monkeypatch.setattr(
        "tracecat.agent.common.config.TRUSTED_MCP_SOCKET_PATH",
        socket_path,
    )
    monkeypatch.setattr(
        runtime_services, "_wait_for_socket", AsyncMock(return_value=False)
    )

    fake_task = AsyncMock()

    def fake_create_task(coro: object) -> AsyncMock:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return fake_task

    monkeypatch.setattr(runtime_services.asyncio, "create_task", fake_create_task)
    stop_mcp_server = AsyncMock()
    monkeypatch.setattr(runtime_services, "stop_mcp_server", stop_mcp_server)

    async def fake_serve() -> None:
        return None

    fake_uvicorn = SimpleNamespace(
        Config=lambda *args, **kwargs: object(),
        Server=lambda config: SimpleNamespace(serve=fake_serve),
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    with pytest.raises(RuntimeError, match="socket was not created"):
        await runtime_services.start_mcp_server()

    stop_mcp_server.assert_awaited_once()


@pytest.mark.anyio
async def test_mark_litellm_unhealthy_notifies_callback_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = AsyncMock()
    monkeypatch.setattr(runtime_services, "_litellm_on_unhealthy", callback)
    monkeypatch.setattr(runtime_services, "_litellm_unhealthy_notified", False)
    monkeypatch.setattr(
        runtime_services,
        "_litellm_status",
        LiteLLMProxyStatus(state="ready", pid=1234),
    )

    await runtime_services._mark_litellm_unhealthy(reason="probe failed")
    await runtime_services._mark_litellm_unhealthy(reason="probe failed again")

    callback.assert_awaited_once()
    assert runtime_services.get_litellm_proxy_status().state == "unhealthy"
    assert runtime_services.get_litellm_proxy_status().reason == "probe failed"


@pytest.mark.anyio
async def test_watch_litellm_process_marks_unhealthy_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = AsyncMock()

    class _FakeProcess:
        pid = 999
        returncode = 7

        async def wait(self) -> int:
            return 7

    process = _FakeProcess()
    monkeypatch.setattr(runtime_services, "_litellm_on_unhealthy", callback)
    monkeypatch.setattr(runtime_services, "_litellm_unhealthy_notified", False)
    monkeypatch.setattr(runtime_services, "_litellm_process", process)
    monkeypatch.setattr(
        runtime_services,
        "_litellm_status",
        LiteLLMProxyStatus(state="ready", pid=process.pid),
    )

    await runtime_services._watch_litellm_process(
        cast(asyncio.subprocess.Process, process)
    )

    callback.assert_awaited_once()
    [status] = [call.args[0] for call in callback.await_args_list]
    assert status.exit_code == 7
    assert status.state == "unhealthy"


@pytest.mark.anyio
async def test_monitor_litellm_health_marks_unhealthy_after_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = AsyncMock()

    class _FakeProcess:
        pid = 321
        returncode: int | None = None

    class _FakeResponse:
        status_code = 503

    class _FakeClient:
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> None:
            del exc_type, exc, tb

        async def get(self, url: str) -> _FakeResponse:
            del url
            return _FakeResponse()

    monkeypatch.setattr(runtime_services, "_litellm_on_unhealthy", callback)
    monkeypatch.setattr(runtime_services, "_litellm_unhealthy_notified", False)
    monkeypatch.setattr(runtime_services, "_litellm_process", _FakeProcess())
    monkeypatch.setattr(
        runtime_services,
        "_litellm_status",
        LiteLLMProxyStatus(state="ready", pid=321),
    )
    monkeypatch.setattr(
        runtime_services, "TRACECAT__LITELLM_HEALTHCHECK_INTERVAL_SECONDS", 0.0
    )
    monkeypatch.setattr(
        runtime_services,
        "TRACECAT__LITELLM_HEALTHCHECK_CONNECT_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        runtime_services,
        "TRACECAT__LITELLM_HEALTHCHECK_READ_TIMEOUT_SECONDS",
        0.02,
    )
    monkeypatch.setattr(
        runtime_services,
        "TRACECAT__LITELLM_HEALTHCHECK_WRITE_TIMEOUT_SECONDS",
        0.03,
    )
    monkeypatch.setattr(
        runtime_services,
        "TRACECAT__LITELLM_HEALTHCHECK_POOL_TIMEOUT_SECONDS",
        0.04,
    )
    monkeypatch.setattr(
        runtime_services, "TRACECAT__LITELLM_HEALTHCHECK_FAILURE_THRESHOLD", 2
    )
    monkeypatch.setattr(
        runtime_services.httpx, "AsyncClient", lambda timeout: _FakeClient()
    )

    await runtime_services._monitor_litellm_health(
        "http://litellm.test/health/readiness"
    )

    callback.assert_awaited_once()
    assert runtime_services.get_litellm_proxy_status().consecutive_probe_failures == 2
    assert runtime_services.get_litellm_proxy_status().state == "unhealthy"


def test_litellm_healthcheck_timeout_uses_per_phase_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_services,
        "TRACECAT__LITELLM_HEALTHCHECK_CONNECT_TIMEOUT_SECONDS",
        1.0,
    )
    monkeypatch.setattr(
        runtime_services,
        "TRACECAT__LITELLM_HEALTHCHECK_READ_TIMEOUT_SECONDS",
        2.0,
    )
    monkeypatch.setattr(
        runtime_services,
        "TRACECAT__LITELLM_HEALTHCHECK_WRITE_TIMEOUT_SECONDS",
        3.0,
    )
    monkeypatch.setattr(
        runtime_services,
        "TRACECAT__LITELLM_HEALTHCHECK_POOL_TIMEOUT_SECONDS",
        4.0,
    )

    timeout = runtime_services._litellm_healthcheck_timeout()

    assert timeout.connect == 1.0
    assert timeout.read == 2.0
    assert timeout.write == 3.0
    assert timeout.pool == 4.0


def test_build_litellm_command_uses_gunicorn_for_multiple_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_services, "TRACECAT__LITELLM_NUM_WORKERS", 4)

    cmd = runtime_services._build_litellm_command(Path("/app/litellm_config.yaml"))

    assert cmd == [
        "litellm",
        "--port",
        "4000",
        "--config",
        "/app/litellm_config.yaml",
        "--num_workers",
        "4",
        "--run_gunicorn",
    ]


@pytest.mark.anyio
async def test_start_configured_llm_proxy_is_noop_for_tracecat_proxy_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_litellm = AsyncMock()
    info = Mock()
    monkeypatch.setattr(
        runtime_services,
        "TRACECAT__LLM_EXECUTION_BACKEND",
        runtime_services.LLMExecutionBackend.TRACECAT_PROXY,
    )
    monkeypatch.setattr(runtime_services, "start_litellm_proxy", start_litellm)
    monkeypatch.setattr(runtime_services.logger, "info", info)

    await runtime_services.start_configured_llm_proxy()

    start_litellm.assert_not_called()
    info.assert_called_once()
