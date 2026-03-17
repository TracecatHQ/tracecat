from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tracecat.agent import runtime_services


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
