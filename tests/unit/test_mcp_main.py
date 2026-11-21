from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from collections.abc import Generator
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastmcp import FastMCP
from sse_starlette.sse import AppStatus
from starlette.testclient import TestClient

from tracecat.db.roles import UnsafeDatabaseLoginError
from tracecat.mcp import __main__ as mcp_main


def _stateless_replica_app():
    server = FastMCP("replica-test")

    @server.tool()
    def ping() -> str:  # pyright: ignore[reportUnusedFunction]
        return "pong"

    return server.http_app(
        path="/mcp",
        transport="streamable-http",
        stateless_http=True,
        json_response=True,
    )


def test_stateless_transport_accepts_followup_on_another_replica() -> None:
    """Initialize on replica A, then list tools through replica B."""
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    with (
        TestClient(_stateless_replica_app()) as replica_a,
        TestClient(_stateless_replica_app()) as replica_b,
    ):
        initialized = replica_a.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "replica-test", "version": "1"},
                },
            },
        )
        listed = replica_b.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )

    assert initialized.status_code == 200
    assert initialized.headers.get("mcp-session-id") is None
    assert listed.status_code == 200
    assert listed.json()["result"]["tools"][0]["name"] == "ping"


def test_run_mcp_server_uses_stateless_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_kwargs: dict[str, object] = {}
    monkeypatch.setattr(AppStatus, "enable_automatic_graceful_drain", True)

    class _MCP:
        def run(self, **kwargs: object) -> None:
            run_kwargs.update(kwargs)

    monkeypatch.delitem(sys.modules, "tracecat.mcp.server", raising=False)
    monkeypatch.setattr(
        mcp_main.importlib,
        "import_module",
        lambda _name: SimpleNamespace(mcp=_MCP()),
    )

    mcp_main._run_mcp_server()

    assert run_kwargs["transport"] == "streamable-http"
    assert run_kwargs["stateless_http"] is True
    assert run_kwargs["uvicorn_config"] == {
        "timeout_graceful_shutdown": (
            mcp_main.TRACECAT_MCP__GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS
        )
    }
    assert AppStatus.enable_automatic_graceful_drain is False


@contextlib.contextmanager
def _no_signal_capture(_server: uvicorn.Server) -> Generator[None]:
    yield


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sse_auto_drain", "expect_result"),
    [
        pytest.param(False, True, id="drain-disabled-result-delivered"),
        pytest.param(True, False, id="sse-starlette-default-result-dropped"),
    ],
)
async def test_sigterm_lets_in_flight_tool_call_finish(
    monkeypatch: pytest.MonkeyPatch, sse_auto_drain: bool, expect_result: bool
) -> None:
    """SIGTERM during a tool call delivers its result only with SSE auto-drain off."""
    monkeypatch.setattr(AppStatus, "enable_automatic_graceful_drain", sse_auto_drain)
    monkeypatch.setattr(AppStatus, "should_exit", False)
    # serve() re-raises captured signals on exit; the simulated SIGTERM below
    # would then kill the test process.
    monkeypatch.setattr(uvicorn.Server, "capture_signals", _no_signal_capture)

    server = FastMCP("drain-test")
    tool_started = asyncio.Event()

    @server.tool()
    async def slow() -> str:  # pyright: ignore[reportUnusedFunction]
        tool_started.set()
        await asyncio.sleep(1)
        return "slow-done"

    app = server.http_app(path="/mcp", transport="streamable-http", stateless_http=True)
    uv_server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            lifespan="on",
            log_level="warning",
            timeout_graceful_shutdown=10,
        )
    )
    serve_task = asyncio.create_task(uv_server.serve())
    async with asyncio.timeout(10):
        while not uv_server.started:
            await asyncio.sleep(0.01)
    port = uv_server.servers[0].sockets[0].getsockname()[1]

    async with httpx.AsyncClient(timeout=10) as client:
        call = asyncio.create_task(
            client.post(
                f"http://127.0.0.1:{port}/mcp",
                headers={
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "slow", "arguments": {}},
                },
            )
        )
        async with asyncio.timeout(5):
            await tool_started.wait()
        uv_server.handle_exit(signal.SIGTERM, None)
        try:
            response = await call
            body = response.text
        except httpx.HTTPError:
            if expect_result:
                raise
            body = ""

    async with asyncio.timeout(15):
        await serve_task

    assert ("slow-done" in body) is expect_result


def test_main_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def _run_mcp_server() -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise ValueError("missing oidc config")

    monkeypatch.setattr(mcp_main, "_run_mcp_server", _run_mcp_server)
    monkeypatch.setattr(mcp_main, "TRACECAT_MCP__STARTUP_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(mcp_main, "TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(mcp_main.time, "sleep", lambda _: None)

    mcp_main.main()

    assert len(attempts) == 3


def test_main_exits_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def _run_mcp_server() -> None:
        attempts.append(1)
        raise ValueError("missing oidc config")

    monkeypatch.setattr(mcp_main, "_run_mcp_server", _run_mcp_server)
    monkeypatch.setattr(mcp_main, "TRACECAT_MCP__STARTUP_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(mcp_main, "TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(mcp_main.time, "sleep", lambda _: None)

    with pytest.raises(SystemExit, match="1"):
        mcp_main.main()

    assert len(attempts) == 3


def test_main_stops_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def _run_mcp_server() -> None:
        attempts.append(1)
        raise KeyboardInterrupt

    monkeypatch.setattr(mcp_main, "_run_mcp_server", _run_mcp_server)
    monkeypatch.setattr(mcp_main, "TRACECAT_MCP__STARTUP_MAX_ATTEMPTS", 3)

    mcp_main.main()

    assert len(attempts) == 1


def test_main_fails_fast_on_unsafe_database_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsafe enforce-mode login is misconfiguration: no retries, no server."""
    calls: list[str] = []

    async def _refuse() -> None:
        calls.append("verify")
        raise UnsafeDatabaseLoginError("superuser")

    def _run_mcp_server() -> None:
        calls.append("run")

    monkeypatch.setattr(mcp_main, "verify_rls_enforceable_login", _refuse)
    monkeypatch.setattr(mcp_main, "_run_mcp_server", _run_mcp_server)
    monkeypatch.setattr(mcp_main, "TRACECAT_MCP__STARTUP_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(mcp_main.time, "sleep", lambda _: None)

    with pytest.raises(SystemExit) as exc_info:
        mcp_main.main()

    assert exc_info.value.code == 1
    assert calls == ["verify"]
