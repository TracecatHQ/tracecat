"""Tests for shared sandbox process utilities."""

from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import patch

import pytest

from tracecat.sandbox.utils import communicate_process_group


class _BlockingProcess:
    """Minimal process double whose communication blocks until cancellation."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.communicate_started = asyncio.Event()
        self.communicate_finished = asyncio.Event()

    async def communicate(
        self,
        input: bytes | None = None,  # noqa: A002
    ) -> tuple[bytes, bytes]:
        del input
        self.communicate_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.communicate_finished.set()
        return b"", b""


@pytest.mark.anyio
async def test_repeated_cancellation_rejoins_process_group_cleanup() -> None:
    """A second cancellation cannot return while group termination is live."""
    fake_process = _BlockingProcess()
    process = cast(asyncio.subprocess.Process, fake_process)
    termination_started = asyncio.Event()
    finish_termination = asyncio.Event()
    termination_finished = asyncio.Event()

    async def blocking_termination(
        requested_process: asyncio.subprocess.Process,
    ) -> None:
        assert requested_process is process
        termination_started.set()
        await finish_termination.wait()
        termination_finished.set()

    with patch(
        "tracecat.sandbox.utils.terminate_process_group",
        side_effect=blocking_termination,
    ):
        communication = asyncio.create_task(communicate_process_group(process))
        await fake_process.communicate_started.wait()
        communication.cancel()
        await termination_started.wait()

        communication.cancel()
        await asyncio.sleep(0)
        assert not communication.done()

        finish_termination.set()
        with pytest.raises(asyncio.CancelledError):
            await communication

    assert termination_finished.is_set()
    assert fake_process.communicate_finished.is_set()
