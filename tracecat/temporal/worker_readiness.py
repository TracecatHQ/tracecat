"""Live readiness checks for workers that have finished startup."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

READINESS_SOCKET = Path("/tmp/tracecat-worker-ready.sock")


async def _respond_ready(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    writer.write(b"ready\n")
    try:
        await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()
        await writer.wait_closed()


@asynccontextmanager
async def worker_readiness(
    path: Path = READINESS_SOCKET,
) -> AsyncIterator[None]:
    """Answer probes only while the initialized Temporal worker is running."""
    server = await asyncio.start_unix_server(_respond_ready, path=path)
    try:
        async with server:
            yield
    finally:
        path.unlink(missing_ok=True)


def check_worker_ready(path: Path = READINESS_SOCKET) -> bool:
    """Require a live response; a socket left by a crashed process is not ready."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(path))
            return client.recv(32) == b"ready\n"
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(0 if check_worker_ready() else 1)
