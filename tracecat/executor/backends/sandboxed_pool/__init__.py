"""Sandboxed worker pool backend package.

This package provides the sandboxed pool executor backend, which uses
a pool of warm nsjail workers for high-throughput execution.

Components:
- backend.py: SandboxedPoolBackend class
- pool.py: SandboxedWorkerPool implementation
- worker.py: Worker process (invoked as module)
"""

from tracecat.executor.backends.sandboxed_pool.backend import SandboxedPoolBackend
from tracecat.executor.backends.sandboxed_pool.pool import (
    SandboxedWorkerInfo,
    SandboxedWorkerPool,
    get_sandboxed_worker_pool,
    shutdown_sandboxed_worker_pool,
)

__all__ = [
    "SandboxedPoolBackend",
    "SandboxedWorkerInfo",
    "SandboxedWorkerPool",
    "get_sandboxed_worker_pool",
    "shutdown_sandboxed_worker_pool",
]
