"""Content minimization and flood behavior for Claude stderr diagnostics."""

import asyncio
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
    assert snapshot["stderr_evicted_lines"] == 1000 - len(snapshot["stderr_tail"])


@pytest.mark.anyio
async def test_forwarder_limits_total_delivery_and_retains_latest_tail() -> None:
    sink = AsyncMock()
    forwarder = StderrForwarder(sink)
    forwarder.start()
    try:
        for _ in range(1000):
            forwarder.capture("ENOENT synthetic-secret")
            await asyncio.sleep(0)
        forwarder.capture("ENOSPC synthetic-secret")
        assert sink.await_count == STDERR_FORWARD_LIMIT
        assert forwarder.tail.snapshot()["stderr_tail"][-1] == (
            "ENOSPC [stderr content withheld]"
        )
    finally:
        await forwarder.close()


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
    forwarder.start()
    forwarder.capture("ENOENT secret")
    await started.wait()
    # Bound even a sink which never returns, then retain diagnostics after failure.
    async with asyncio.timeout(1):
        while not forwarder.delivery_failed:
            await asyncio.sleep(0.01)
    for _ in range(1000):
        forwarder.capture("ENOSPC secret")
    assert forwarder.tail.snapshot()["stderr_lines"] == 1001
    await forwarder.close()
