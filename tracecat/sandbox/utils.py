"""Shared sandbox utilities.

Provides common utilities used by both Python script sandbox (tracecat/sandbox/)
and agent runtime sandbox (tracecat/agent/sandbox/).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
from contextlib import suppress
from pathlib import Path

from tracecat.config import (
    TRACECAT__DISABLE_NSJAIL,
    TRACECAT__SANDBOX_NSJAIL_PATH,
    TRACECAT__SANDBOX_ROOTFS_PATH,
)

_PID_NAMESPACE_AVAILABLE: bool | None = None
_PID_NAMESPACE_PROBE_ERROR: str | None = None
_PROCESS_EXIT_POLL_INTERVAL_SECONDS = 0.01


async def terminate_process_group(process: asyncio.subprocess.Process) -> None:
    """Kill and reap a subprocess session, including any surviving descendants.

    The subprocess must have been started with ``start_new_session=True``, which
    makes its PID the process-group ID. Calling this after normal completion is
    intentional: a subprocess can exit while leaving background descendants
    alive.
    """
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    await process.wait()


async def _finish_process_group_cleanup(
    process: asyncio.subprocess.Process,
    communicate_task: asyncio.Task[tuple[bytes | None, bytes | None]],
    termination_task: asyncio.Task[None] | None,
) -> None:
    """Finish process termination and consume the communication task."""
    if termination_task is None:
        termination_task = asyncio.create_task(terminate_process_group(process))
    try:
        await termination_task
    finally:
        if not communicate_task.done():
            communicate_task.cancel()
        with suppress(asyncio.CancelledError):
            await communicate_task


async def _rejoin_cleanup_through_cancellation(
    cleanup_task: asyncio.Task[None],
) -> None:
    """Wait for cleanup despite repeated caller cancellation."""
    pending_cancellation: asyncio.CancelledError | None = None
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as e:
            if cleanup_task.cancelled():
                raise
            pending_cancellation = e

    try:
        cleanup_task.result()
    except BaseException as cleanup_error:
        if pending_cancellation is not None:
            raise pending_cancellation from cleanup_error
        raise
    if pending_cancellation is not None:
        raise pending_cancellation


async def communicate_process_group(
    process: asyncio.subprocess.Process,
    *,
    input: bytes | None = None,  # noqa: A002
    timeout: float | None = None,
) -> tuple[bytes, bytes]:
    """Communicate with a process while containing its process group.

    A background descendant can inherit the leader's output pipes, causing both
    ``communicate()`` and asyncio's ``wait()`` to wait after the leader exits.
    Polling ``returncode`` observes the leader exit independently, letting us
    terminate the group immediately and close those pipes. Cancellation also
    terminates the group before it propagates. Cleanup runs in an independent
    task and is rejoined through repeated cancellation so callers cannot release
    resources while the process group is still alive.
    """
    communicate_task = asyncio.create_task(process.communicate(input=input))
    termination_task: asyncio.Task[None] | None = None
    operation_error: BaseException | None = None
    try:
        async with asyncio.timeout(timeout):
            while process.returncode is None:
                await asyncio.sleep(_PROCESS_EXIT_POLL_INTERVAL_SECONDS)
            termination_task = asyncio.create_task(terminate_process_group(process))
            await asyncio.shield(termination_task)
            stdout, stderr = await communicate_task
    except BaseException as e:
        operation_error = e
        raise
    finally:
        cleanup_task = asyncio.create_task(
            _finish_process_group_cleanup(
                process,
                communicate_task,
                termination_task,
            )
        )
        try:
            await _rejoin_cleanup_through_cancellation(cleanup_task)
        except BaseException as cleanup_error:
            if operation_error is not None:
                raise operation_error from cleanup_error
            raise

    if stdout is None or stderr is None:
        raise RuntimeError("Captured stdout and stderr are required")
    return stdout, stderr


def is_nsjail_available() -> bool:
    """Check if nsjail sandbox is available and configured.

    This function is used by both the Python script sandbox and the agent
    runtime sandbox to determine if nsjail isolation is available.

    Returns:
        True if nsjail can be used, False otherwise.
    """
    # Check the appropriate disable flag
    if TRACECAT__DISABLE_NSJAIL:
        return False

    nsjail_path = Path(TRACECAT__SANDBOX_NSJAIL_PATH)
    rootfs_path = Path(TRACECAT__SANDBOX_ROOTFS_PATH)

    return nsjail_path.exists() and rootfs_path.is_dir()


async def pid_namespace_available() -> bool:
    """Check whether ``unshare --pid --fork --kill-child`` works on this host.

    The result and any failure reason are cached for the process lifetime.
    """
    global _PID_NAMESPACE_AVAILABLE, _PID_NAMESPACE_PROBE_ERROR
    if _PID_NAMESPACE_AVAILABLE is not None:
        return _PID_NAMESPACE_AVAILABLE

    if shutil.which("unshare") is None:
        _PID_NAMESPACE_PROBE_ERROR = "unshare binary not found"
        _PID_NAMESPACE_AVAILABLE = False
        return False

    probe: asyncio.subprocess.Process | None = None
    try:
        probe = await asyncio.create_subprocess_exec(
            "unshare",
            "--pid",
            "--fork",
            "--kill-child",
            "true",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await asyncio.wait_for(probe.wait(), timeout=2)
        _PID_NAMESPACE_AVAILABLE = probe.returncode == 0
        _PID_NAMESPACE_PROBE_ERROR = (
            None
            if _PID_NAMESPACE_AVAILABLE
            else f"unshare probe exited with status {probe.returncode}"
        )
    except TimeoutError:
        if probe is not None:
            with suppress(ProcessLookupError):
                probe.kill()
            await probe.wait()
        _PID_NAMESPACE_PROBE_ERROR = "unshare probe timed out"
        _PID_NAMESPACE_AVAILABLE = False
    except Exception as e:
        if probe is not None:
            with suppress(ProcessLookupError):
                probe.kill()
            await probe.wait()
        _PID_NAMESPACE_PROBE_ERROR = f"unshare probe failed: {e}"
        _PID_NAMESPACE_AVAILABLE = False
    return _PID_NAMESPACE_AVAILABLE


def pid_namespace_probe_error() -> str | None:
    """Return the cached PID namespace probe failure reason, if any."""
    return _PID_NAMESPACE_PROBE_ERROR
