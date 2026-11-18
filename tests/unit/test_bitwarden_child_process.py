"""A hanging Bitwarden server cannot stall the executor or leak the token.

Runs the real ``bitwarden-sdk`` binary in the real child process against a
local TCP listener that accepts connections and never answers, so the SDK's
TLS handshake waits forever (the SDK has no timeout of its own). Linux only:
the child's argument vector and environment are read from ``/proc``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from loguru import logger
from tracecat_ee.secrets.providers import bitwarden_secrets_manager as bw

from tracecat.secrets.enums import SecretResolutionErrorCode
from tracecat.secrets.schemas import BitwardenSecretsManagerStoreConfig
from tracecat.secrets.types import Failure

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.slow,
    pytest.mark.skipif(
        sys.platform != "linux", reason="reads the child's /proc entries"
    ),
]

# Well-formed (client ID, secret, 16-byte key) so the SDK sends a request.
TOKEN = "0.5f1c9a3e-2b7d-4c8e-9f10-3a4b5c6d7e8f.plantedsecret:cGxhbnRlZGtleTEyMzQ1Ng=="
TIMEOUT_SECONDS = 3.0
LOOP_LAG_BOUND_SECONDS = 0.1
"""The suite's idle event-loop lag is a few milliseconds; a blocked loop is seconds."""


@pytest.fixture
async def silent_server() -> AsyncIterator[tuple[int, list[asyncio.StreamWriter]]]:
    """A TCP listener that accepts connections and never sends a byte."""
    connections: list[asyncio.StreamWriter] = []

    async def hold(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connections.append(writer)

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port, connections
    finally:
        server.close()
        for writer in connections:
            writer.close()


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[asyncio.subprocess.Process]:
    """Record the real child processes the provider starts."""
    processes: list[asyncio.subprocess.Process] = []
    real = asyncio.create_subprocess_exec

    async def recording(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await real(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(bw.asyncio, "create_subprocess_exec", recording)
    monkeypatch.setattr(bw, "FETCH_TIMEOUT_SECONDS", TIMEOUT_SECONDS)
    # A Tracecat key in the parent's environment must not reach the child.
    monkeypatch.setenv("TRACECAT__DB_ENCRYPTION_KEY", "planted-tracecat-key")
    return processes


@pytest.fixture
def captured_logs() -> Iterator[list[str]]:
    lines: list[str] = []
    sink = logger.add(lines.append, level="TRACE", format="{message} | {extra}")
    yield lines
    logger.remove(sink)


async def _max_loop_lag(stop: asyncio.Event) -> float:
    worst = 0.0
    while not stop.is_set():
        started = time.monotonic()
        await asyncio.sleep(0.01)
        worst = max(worst, time.monotonic() - started - 0.01)
    return worst


async def _wait_for_hang(
    spawned: list[asyncio.subprocess.Process],
    connections: list[asyncio.StreamWriter],
) -> int:
    """Wait until the child is running and connected to the silent server."""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while not (spawned and connections):
        assert time.monotonic() < deadline, (
            f"child never connected: spawned={[p.returncode for p in spawned]}"
            f" connections={len(connections)}"
        )
        await asyncio.sleep(0.02)
    return spawned[0].pid


def _assert_proc_clean(pid: int) -> None:
    with open(f"/proc/{pid}/cmdline", "rb") as f:
        cmdline = f.read()
    with open(f"/proc/{pid}/environ", "rb") as f:
        environ = f.read()
    assert TOKEN.encode() not in cmdline
    assert TOKEN.encode() not in environ
    assert b"TRACECAT__" not in environ
    assert b"TRACECAT__" not in cmdline


def _assert_gone(pid: int) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def _config(port: int) -> BitwardenSecretsManagerStoreConfig:
    return BitwardenSecretsManagerStoreConfig(
        server=f"https://127.0.0.1:{port}", organization_id=uuid.uuid4()
    )


async def test_hanging_server_times_out_without_stalling_or_leaking(
    silent_server: tuple[int, list[asyncio.StreamWriter]],
    spawned: list[asyncio.subprocess.Process],
    captured_logs: list[str],
) -> None:
    port, connections = silent_server
    stop = asyncio.Event()
    lag = asyncio.create_task(_max_loop_lag(stop))
    started = time.monotonic()
    fetch = asyncio.create_task(
        bw.fetch_with_child(_config(port), TOKEN, [str(uuid.uuid4())])
    )

    # While the child hangs, its argv and environment hold no secrets
    pid = await _wait_for_hang(spawned, connections)
    _assert_proc_clean(pid)

    # It fails with timeout within the configured limit
    outcomes = await fetch
    elapsed = time.monotonic() - started
    stop.set()
    assert [o.code for o in outcomes.values() if isinstance(o, Failure)] == [
        SecretResolutionErrorCode.TIMEOUT
    ]
    assert elapsed < TIMEOUT_SECONDS + 1.0
    # the event loop kept running
    assert await lag < LOOP_LAG_BOUND_SECONDS
    # the child is gone, and the token never reached the logs
    _assert_gone(pid)
    assert TOKEN not in "\n".join(captured_logs)


async def test_cancelled_resolve_kills_the_child(
    silent_server: tuple[int, list[asyncio.StreamWriter]],
    spawned: list[asyncio.subprocess.Process],
    captured_logs: list[str],
) -> None:
    port, connections = silent_server
    fetch = asyncio.create_task(
        bw.fetch_with_child(_config(port), TOKEN, [str(uuid.uuid4())])
    )
    pid = await _wait_for_hang(spawned, connections)

    fetch.cancel()
    with pytest.raises(asyncio.CancelledError):
        await fetch

    _assert_gone(pid)
    assert TOKEN not in "\n".join(captured_logs)
