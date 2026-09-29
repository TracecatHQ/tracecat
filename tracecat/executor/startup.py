"""Validate execution prerequisites before a worker accepts tasks."""

import os
from pathlib import Path

from tracecat import config
from tracecat.executor.enums import ExecutorBackendType


def validate_execution_backend() -> None:
    """Reject missing nsjail prerequisites for explicitly sandboxed workers."""
    if config.TRACECAT__EXECUTOR_BACKEND != ExecutorBackendType.NSJAIL:
        return

    nsjail = Path(config.TRACECAT__SANDBOX_NSJAIL_PATH)
    if not nsjail.is_file() or not os.access(nsjail, os.X_OK):
        raise RuntimeError(
            "TRACECAT__EXECUTOR_BACKEND=nsjail requires an executable nsjail "
            f"binary at {nsjail}. Check TRACECAT__SANDBOX_NSJAIL_PATH."
        )

    rootfs = Path(config.TRACECAT__SANDBOX_ROOTFS_PATH)
    if not rootfs.is_dir():
        raise RuntimeError(
            "TRACECAT__EXECUTOR_BACKEND=nsjail requires a sandbox rootfs "
            f"directory at {rootfs}. Check TRACECAT__SANDBOX_ROOTFS_PATH."
        )
