"""Shared sandbox utilities.

Provides common utilities used by both Python script sandbox (tracecat/sandbox/)
and agent runtime sandbox (tracecat/agent/sandbox/).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from contextlib import suppress
from pathlib import Path

from tracecat.config import (
    TRACECAT__DISABLE_NSJAIL,
    TRACECAT__SANDBOX_NSJAIL_PATH,
    TRACECAT__SANDBOX_ROOTFS_PATH,
)

_PID_NAMESPACE_AVAILABLE: bool | None = None


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

    Mirrors the probe used by ``UnsafePidExecutor``; the result is cached for
    the process lifetime.
    """
    global _PID_NAMESPACE_AVAILABLE
    if _PID_NAMESPACE_AVAILABLE is not None:
        return _PID_NAMESPACE_AVAILABLE

    if shutil.which("unshare") is None:
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
    except Exception:
        if probe is not None:
            with suppress(ProcessLookupError):
                probe.kill()
            await probe.wait()
        _PID_NAMESPACE_AVAILABLE = False
    return _PID_NAMESPACE_AVAILABLE
