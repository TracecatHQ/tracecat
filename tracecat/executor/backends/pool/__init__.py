"""Worker pool backend package.

This package provides the pool executor backend, which uses
a pool of warm nsjail workers for high-throughput execution.

Components:
- backend.py: PoolBackend class
- pool.py: WorkerPool implementation
- worker.py: Worker process (invoked as module)
"""

from tracecat.executor.backends.pool.backend import PoolBackend
from tracecat.executor.backends.pool.pool import (
    WorkerInfo,
    WorkerPool,
    get_worker_pool,
    shutdown_worker_pool,
)

__all__ = [
    "PoolBackend",
    "WorkerInfo",
    "WorkerPool",
    "get_worker_pool",
    "shutdown_worker_pool",
]
