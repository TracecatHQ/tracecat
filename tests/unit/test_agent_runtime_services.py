from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from tracecat.agent import runtime_services
from tracecat.agent.otel_delivery import OtelDelivery, collector_client


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
async def test_start_and_stop_claude_runtime_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_broker = Mock()
    fake_broker.start = AsyncMock()
    fake_broker.stop = AsyncMock()
    monkeypatch.setattr(
        "tracecat.agent.runtime.claude_code.broker.ClaudeRuntimeBroker",
        Mock(return_value=fake_broker),
    )
    runtime_services._claude_runtime_broker = None

    await runtime_services.start_claude_runtime_broker()
    assert runtime_services.get_claude_runtime_broker() is fake_broker
    fake_broker.start.assert_awaited_once()

    await runtime_services.stop_claude_runtime_broker()
    fake_broker.stop.assert_awaited_once()


@pytest.mark.anyio
async def test_otel_delivery_pool_keeps_proxy_env_and_drains_within_stop_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker's pool trusts proxy env vars and drains inside Fargate's 30 s stop."""
    monkeypatch.setattr(runtime_services, "_otel_delivery_pool", None)

    runtime_services.start_otel_delivery_pool()
    pool = runtime_services.get_otel_delivery_pool()
    assert pool.client.trust_env is True
    assert pool.limits.drain_seconds < 30

    await runtime_services.stop_otel_delivery_pool()
    assert pool.client.is_closed
    with pytest.raises(RuntimeError, match="not started"):
        runtime_services.get_otel_delivery_pool()


@pytest.mark.anyio
async def test_stopping_the_otel_delivery_pool_delivers_admitted_batches_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker shutdown delivers what was admitted before closing the client."""
    release = asyncio.Event()
    captured: list[httpx.Request] = []

    async def collector(request: httpx.Request) -> httpx.Response:
        await release.wait()
        captured.append(request)
        return httpx.Response(200)

    def mock_collector_client(*, trust_env: bool) -> httpx.AsyncClient:
        return collector_client(
            trust_env=trust_env, transport=httpx.MockTransport(collector)
        )

    monkeypatch.setattr(runtime_services, "_otel_delivery_pool", None)
    monkeypatch.setattr(runtime_services, "collector_client", mock_collector_client)
    runtime_services.start_otel_delivery_pool()
    pool = runtime_services.get_otel_delivery_pool()
    organization_id = uuid.uuid4()
    reservation = pool.reserve(organization_id, 4)
    assert reservation is not None
    assert pool.admit(
        reservation,
        [
            OtelDelivery(
                share=organization_id,
                signal="/v1/logs",
                url="https://collector.example.com/v1/logs",
                headers={},
                content_type="application/x-protobuf",
                body=b"logs",
                organization_id=organization_id,
                workspace_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
            )
        ],
    )
    reservation.close()
    # The collector answers only once shutdown has started waiting.
    asyncio.get_running_loop().call_later(0.05, release.set)

    await runtime_services.stop_otel_delivery_pool()

    assert len(captured) == 1
    assert pool.client.is_closed
