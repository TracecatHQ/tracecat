"""Validate execution prerequisites before a worker accepts tasks."""

import os
from collections.abc import Iterator
from contextlib import AbstractAsyncContextManager, contextmanager
from pathlib import Path

from tracecat import config
from tracecat.temporal.worker_lifecycle import WORKER_READY_FILE, worker_readiness


def validate_execution_backend() -> None:
    """Reject missing nsjail prerequisites for explicitly sandboxed workers."""
    if not config.TRACECAT__EXECUTOR_BACKEND.uses_nsjail:
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


@contextmanager
def executor_lifecycle() -> Iterator[AbstractAsyncContextManager[None]]:
    """Validate startup and provide readiness for the initialized worker.

    Enter the yielded context after the Temporal worker so readiness is removed
    before Temporal drains. The outer context also cleans up on startup failure.
    """
    path = WORKER_READY_FILE
    path.unlink(missing_ok=True)
    validate_execution_backend()
    try:
        yield worker_readiness(path)
    finally:
        path.unlink(missing_ok=True)
