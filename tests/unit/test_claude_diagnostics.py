"""Content minimization and flood behavior for Claude stderr diagnostics."""

import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock

import pytest

from tracecat.agent.runtime.claude_code.diagnostics import (
    STDERR_FORWARD_LIMIT,
    STDERR_MAX_BYTES,
    STDERR_MAX_LINES,
    StderrForwarder,
    StderrTail,
    summarize_stderr,
)


@pytest.mark.parametrize(
    "line",
    [
        "Authorization: Bearer synthetic-secret ECONNREFUSED https://example.com/private",
        'MCP arguments: ["--token", "synthetic-secret"] ECONNREFUSED',
        '{"prompt": "synthetic payload", "error": "ECONNREFUSED"}',
        "ENV_VALUE=synthetic-secret\nECONNREFUSED /private/synthetic-path",
    ],
)
def test_stderr_withholds_all_free_form_content(line: str) -> None:
    assert summarize_stderr(line) == "ECONNREFUSED [stderr content withheld]"
    assert summarize_stderr("arbitrary customer content") == "[stderr content withheld]"


@pytest.mark.parametrize(
    "line",
    [
        "synthetic payload",
        "ECONNREFUSED ECONNRESET ETIMEDOUT ENOTFOUND EACCES ENOENT ENOMEM ENOSPC "
        "CERT_HAS_EXPIRED UNABLE_TO_VERIFY_LEAF_SIGNATURE",
        "🔒" * 100_000,
    ],
    ids=["unknown", "many-signals", "large-unicode"],
)
def test_stderr_tail_bounds_lines_and_utf8_bytes(line: str) -> None:
    tail = StderrTail()
    for _ in range(1000):
        tail.append(line)
    snapshot = tail.snapshot()
    assert snapshot["stderr_lines"] == 1000
    assert len(snapshot["stderr_tail"]) <= STDERR_MAX_LINES
    assert snapshot["stderr_tail_bytes"] <= STDERR_MAX_BYTES
    assert len("\n".join(snapshot["stderr_tail"]).encode()) <= STDERR_MAX_BYTES
    assert snapshot["stderr_evicted_lines"] == (
        1000 - snapshot["stderr_withheld_lines"] - len(snapshot["stderr_tail"])
    )
    if line == "synthetic payload" or line.startswith("🔒"):
        assert snapshot["stderr_withheld_lines"] == 1000
        assert snapshot["stderr_tail"] == []


@pytest.mark.anyio
async def test_forwarder_limits_total_delivery() -> None:
    sink = AsyncMock()
    forwarder = StderrForwarder(sink)
    task = asyncio.create_task(forwarder.run())
    try:
        for _ in range(1000):
            forwarder.capture("ENOENT synthetic-secret")
            await asyncio.sleep(0)
        forwarder.capture("ENOSPC synthetic-secret")
        assert sink.await_count == STDERR_FORWARD_LIMIT
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.anyio
@pytest.mark.parametrize("blocked", [False, True])
async def test_sink_failure_does_not_stop_capture(blocked: bool) -> None:
    started = asyncio.Event()

    async def sink(_summary: str) -> None:
        started.set()
        if blocked:
            await asyncio.Event().wait()
        raise RuntimeError("synthetic sink failure")

    forwarder = StderrForwarder(sink)
    task = asyncio.create_task(forwarder.run())
    forwarder.capture("ENOENT secret")
    await started.wait()
    # A failed or blocked sink terminates the forwarder; later capture is
    # bounded and cannot block the transport, which owns the retained tail.
    async with asyncio.timeout(1):
        await task
    for _ in range(1000):
        forwarder.capture("ENOSPC " + "🔒" * 1000)
    assert forwarder._queue.qsize() <= STDERR_FORWARD_LIMIT
    while not forwarder._queue.empty():
        assert len(forwarder._queue.get_nowait().encode()) <= 256
