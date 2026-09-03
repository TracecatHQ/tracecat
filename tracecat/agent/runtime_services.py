"""Worker-global runtime services for agent execution workers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from tracecat.logger import logger

_mcp_server_task: asyncio.Task[None] | None = None

if TYPE_CHECKING:
    from tracecat.agent.runtime.claude_code.broker import ClaudeRuntimeBroker

_claude_runtime_broker: ClaudeRuntimeBroker | None = None


async def _wait_for_socket(
    socket_path: Path,
    *,
    attempts: int = 50,
    interval: float = 0.1,
) -> bool:
    """Wait for a Unix socket path to appear."""
    for _ in range(attempts):
        if socket_path.exists():
            return True
        await asyncio.sleep(interval)
    return False


async def start_mcp_server() -> None:
    """Start the trusted MCP HTTP server on Unix socket."""
    global _mcp_server_task

    import uvicorn

    from tracecat.agent.common.config import TRUSTED_MCP_SOCKET_PATH
    from tracecat.agent.mcp.trusted_server import app

    socket_path = TRUSTED_MCP_SOCKET_PATH
    socket_path.parent.mkdir(parents=True, exist_ok=True)

    if socket_path.exists():
        socket_path.unlink()

    logger.info("Starting MCP server", socket_path=str(socket_path))
    uvicorn_config = uvicorn.Config(
        app,
        uds=str(socket_path),
        log_level="warning",
    )
    server = uvicorn.Server(uvicorn_config)
    _mcp_server_task = asyncio.create_task(server.serve())

    if not await _wait_for_socket(socket_path):
        await stop_mcp_server()
        raise RuntimeError(f"MCP server socket was not created at {socket_path}")

    os.chmod(str(socket_path), 0o600)
    logger.info("MCP server started", socket_path=str(socket_path))


async def stop_mcp_server() -> None:
    """Stop the MCP server."""
    global _mcp_server_task

    if _mcp_server_task:
        logger.info("Stopping MCP server")
        _mcp_server_task.cancel()
        try:
            await _mcp_server_task
        except asyncio.CancelledError:
            pass
        _mcp_server_task = None


async def start_claude_runtime_broker() -> None:
    """Start the worker-global Claude runtime broker."""
    global _claude_runtime_broker

    from tracecat.agent.runtime.claude_code.broker import ClaudeRuntimeBroker

    if _claude_runtime_broker is not None:
        return
    broker = ClaudeRuntimeBroker()
    await broker.start()
    _claude_runtime_broker = broker
    logger.info("Claude runtime broker started")


async def stop_claude_runtime_broker() -> None:
    """Stop the worker-global Claude runtime broker."""
    global _claude_runtime_broker

    if _claude_runtime_broker is None:
        return
    broker = _claude_runtime_broker
    _claude_runtime_broker = None
    await broker.stop()
    logger.info("Claude runtime broker stopped")


def get_claude_runtime_broker() -> ClaudeRuntimeBroker:
    """Return the initialized worker-global Claude runtime broker."""
    if _claude_runtime_broker is None:
        raise RuntimeError("Claude runtime broker is not started")
    return _claude_runtime_broker
