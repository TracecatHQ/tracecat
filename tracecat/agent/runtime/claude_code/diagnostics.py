"""Bounded, content-minimized diagnostics for untrusted Claude CLI stderr."""

from __future__ import annotations

import asyncio
import re
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TypedDict

STDERR_MAX_LINES = 64
STDERR_MAX_BYTES = 4096
STDERR_READ_BYTES = 4096
DIAGNOSTIC_TIMEOUT_SECONDS = 0.25
# A lifetime budget prevents a fast sink from turning a child flood into a log flood.
STDERR_FORWARD_LIMIT = 32

# Exact-secret masking and MCP argument sanitization cannot protect arbitrary
# prompts, payloads, paths, or unknown credentials. Emit only fixed vocabulary;
# never interpolate any part of the original line (including regex captures).
_SAFE_SIGNALS = (
    "ECONNREFUSED",
    "ECONNRESET",
    "ETIMEDOUT",
    "ENOTFOUND",
    "EACCES",
    "ENOENT",
    "ENOMEM",
    "ENOSPC",
    "CERT_HAS_EXPIRED",
    "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
)
_SIGNAL_PATTERNS = tuple(
    (signal, re.compile(rf"\b{signal}\b")) for signal in _SAFE_SIGNALS
)


def summarize_stderr(line: str) -> str:
    """Retain only allowlisted error signals from a bounded prefix of stderr."""
    prefix = line[:STDERR_READ_BYTES]
    signals = [signal for signal, pattern in _SIGNAL_PATTERNS if pattern.search(prefix)]
    return " ".join([*signals, "[stderr content withheld]"])


class StderrSnapshot(TypedDict):
    stderr_lines: int
    stderr_tail: list[str]
    stderr_tail_bytes: int
    stderr_withheld_lines: int
    stderr_evicted_lines: int


class StderrTail:
    """Retain a line- and byte-bounded tail containing no free-form child text."""

    def __init__(self) -> None:
        self._lines: deque[str] = deque()
        self._bytes = 0
        self._total = 0
        self._withheld = 0

    def append(self, line: str) -> str | None:
        """Minimize a line before retaining it and return its safe summary."""
        summary = summarize_stderr(line)
        self._total += 1
        if summary == "[stderr content withheld]":
            self._withheld += 1
            return None
        self._lines.append(summary)
        self._bytes += len(summary.encode()) + 1
        while len(self._lines) > STDERR_MAX_LINES or self._bytes > STDERR_MAX_BYTES:
            self._bytes -= len(self._lines.popleft().encode()) + 1
        return summary

    def snapshot(self) -> StderrSnapshot:
        """Return safe diagnostics suitable for structured logging."""
        return {
            "stderr_lines": self._total,
            "stderr_tail": list(self._lines),
            "stderr_tail_bytes": self._bytes,
            "stderr_withheld_lines": self._withheld,
            "stderr_evicted_lines": self._total - self._withheld - len(self._lines),
        }


class StderrForwarder:
    """Deliver at most 32 transport-minimized summaries; retain no second tail."""

    def __init__(self, send: Callable[[str], Awaitable[None]]) -> None:
        self._send = send
        # Each item is capped at 256 UTF-8 bytes, bounding queued bytes as well
        # as lines. The transport owns sanitization and retained diagnostics.
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=STDERR_FORWARD_LIMIT)

    def capture(self, summary: str) -> None:
        """Enqueue a safe transport summary without blocking the child's pipe."""
        with suppress(asyncio.QueueFull):
            self._queue.put_nowait(summary.encode()[:256].decode(errors="ignore"))

    async def run(self) -> None:
        """Stop on sink failure or after the per-turn delivery budget is spent."""
        with suppress(Exception):
            for _ in range(STDERR_FORWARD_LIMIT):
                summary = await self._queue.get()
                async with asyncio.timeout(DIAGNOSTIC_TIMEOUT_SECONDS):
                    await self._send(summary)
