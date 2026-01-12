"""Shared sandbox utilities.

Provides common utilities used by both Python script sandbox (tracecat/sandbox/)
and agent runtime sandbox (tracecat/agent/sandbox/).
"""

from __future__ import annotations

from pathlib import Path

from tracecat.config import (
    TRACECAT__DISABLE_NSJAIL,
    TRACECAT__SANDBOX_NSJAIL_PATH,
    TRACECAT__SANDBOX_ROOTFS_PATH,
)


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
