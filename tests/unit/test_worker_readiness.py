"""Worker readiness requires a live listener after startup."""

import asyncio
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from tracecat.temporal.worker_readiness import check_worker_ready, worker_readiness


@pytest.fixture
def path() -> Iterator[Path]:
    # macOS pytest temp directories can exceed the Unix socket path limit.
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="worker-ready-") as directory:
        yield Path(directory) / "ready.sock"


@pytest.mark.anyio
async def test_ready_only_inside_worker_lifetime(path: Path) -> None:
    assert not check_worker_ready(path)
    async with worker_readiness(path):
        assert await asyncio.to_thread(check_worker_ready, path)
    assert not check_worker_ready(path)
    assert not path.exists()


@pytest.mark.anyio
async def test_stale_socket_is_not_ready_and_can_be_replaced(path: Path) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
        stale.bind(str(path))
    assert path.exists()
    assert not check_worker_ready(path)
    async with worker_readiness(path):
        assert await asyncio.to_thread(check_worker_ready, path)


@pytest.mark.anyio
async def test_worker_failure_removes_readiness(path: Path) -> None:
    with pytest.raises(RuntimeError, match="worker failed"):
        async with worker_readiness(path):
            assert await asyncio.to_thread(check_worker_ready, path)
            raise RuntimeError("worker failed")
    assert not check_worker_ready(path)
