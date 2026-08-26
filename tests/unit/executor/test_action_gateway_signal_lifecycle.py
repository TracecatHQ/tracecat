from __future__ import annotations

import asyncio
import multiprocessing
import os
import signal
import sys
import tempfile
import traceback
from collections.abc import Iterator
from multiprocessing.connection import Connection
from pathlib import Path

import httpx
import pytest

from tracecat.executor.action_gateway.server import ActionGateway
from tracecat.temporal.worker_lifecycle import (
    install_worker_shutdown_signal_handlers,
)

COLD_PROCESS_START_TIMEOUT_SECONDS = 60.0


@pytest.fixture(autouse=True, scope="session")
def default_org() -> Iterator[None]:
    yield


@pytest.fixture(autouse=True, scope="session")
def workflow_bucket() -> Iterator[None]:
    yield


@pytest.fixture(autouse=True)
def clean_redis_db() -> Iterator[None]:
    yield


async def _gateway_is_reachable(socket_path: Path) -> bool:
    try:
        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(socket_path)),
        ) as client:
            response = await client.get("http://action-gateway/internal/health")
    except httpx.ConnectError:
        return False
    return response.status_code == 200


async def _run_signal_probe(
    socket_path: Path,
    result_connection: Connection,
) -> None:
    shutdown_event = asyncio.Event()
    gateway = ActionGateway(socket_path=socket_path)

    with install_worker_shutdown_signal_handlers(shutdown_event):
        await gateway.start()
        try:
            result_connection.send(("ready", os.getpid()))
            await asyncio.wait_for(shutdown_event.wait(), timeout=5)

            # Give the embedded server time to react to SIGTERM. It must remain
            # available until the executor explicitly stops it after draining.
            await asyncio.sleep(0.25)
            result_connection.send(
                ("after-signal", await _gateway_is_reachable(socket_path))
            )

            loop = asyncio.get_running_loop()
            command = await loop.run_in_executor(None, result_connection.recv)
            if command != "stop":
                raise RuntimeError(f"Unexpected parent command: {command!r}")
        finally:
            await gateway.stop()


def _run_action_gateway_signal_probe(
    socket_path: str,
    result_connection: Connection,
) -> None:
    try:
        asyncio.run(_run_signal_probe(Path(socket_path), result_connection))
    except BaseException:
        result_connection.send(("error", traceback.format_exc()))
        raise
    finally:
        result_connection.close()


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX signals")
def test_sigterm_keeps_action_gateway_available_during_worker_drain() -> None:
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=True)
    with tempfile.TemporaryDirectory(prefix="tc-gw-") as temp_dir:
        process = context.Process(
            target=_run_action_gateway_signal_probe,
            args=(str(Path(temp_dir) / "gateway.sock"), child_connection),
        )

        process.start()
        child_connection.close()
        try:
            assert parent_connection.poll(COLD_PROCESS_START_TIMEOUT_SECONDS), (
                "Action Gateway did not start"
            )
            status, detail = parent_connection.recv()
            if status == "error":
                pytest.fail(detail)
            assert status == "ready"

            os.kill(detail, signal.SIGTERM)

            assert parent_connection.poll(10), "Executor did not handle SIGTERM"
            status, detail = parent_connection.recv()
            if status == "error":
                pytest.fail(detail)
            assert status == "after-signal"
            assert detail is True
        finally:
            if process.is_alive():
                try:
                    parent_connection.send("stop")
                except (BrokenPipeError, EOFError, OSError):
                    pass
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join()
            parent_connection.close()

    assert process.exitcode == 0
