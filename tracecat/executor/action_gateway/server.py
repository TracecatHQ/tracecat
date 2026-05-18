"""Worker-owned lifecycle for the executor-local action gateway."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from tracecat import config
from tracecat.executor.action_gateway.app import create_app
from tracecat.executor.action_gateway.config import action_gateway_socket_path
from tracecat.logger import logger


class ActionGateway:
    """Executor-local gateway for action SDK calls backed by a Unix socket."""

    def __init__(self, *, socket_path: Path | None = None) -> None:
        self._configured_socket_path = socket_path
        self._server: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._socket_path: Path | None = None

    async def start(self) -> None:
        """Start the action gateway when enabled for this executor process."""
        if not config.TRACECAT__ACTION_GATEWAY_ENABLED:
            return
        if self._task is not None:
            return

        import uvicorn

        socket_path = self._configured_socket_path or action_gateway_socket_path()
        if socket_path is None:
            return
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)

        uvicorn_config = uvicorn.Config(
            create_app(),
            uds=str(socket_path),
            log_level="warning",
            lifespan="on",
        )
        server = uvicorn.Server(uvicorn_config)
        self._server = server
        self._socket_path = socket_path
        self._task = asyncio.create_task(server.serve())

        for _ in range(50):
            if server.started and socket_path.exists():
                os.chmod(socket_path, 0o600)
                logger.info(
                    "Action Gateway started",
                    socket_path=str(socket_path),
                )
                return
            if self._task.done():
                await self._task
            await asyncio.sleep(0.1)

        await self.stop()
        raise RuntimeError("Action Gateway did not start within 5s")

    async def stop(self) -> None:
        """Stop the action gateway if it was started."""
        server = self._server
        socket_path = self._socket_path
        task = self._task
        self._server = None
        self._socket_path = None
        self._task = None
        if task is None:
            return

        if server is not None and hasattr(server, "should_exit"):
            server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=5)
        except TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if socket_path is not None:
            socket_path.unlink(missing_ok=True)
        logger.info("Action Gateway stopped")
