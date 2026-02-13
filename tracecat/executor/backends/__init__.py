"""Executor backend implementations.

This module provides functions for managing the executor backend lifecycle.
The backend must be initialized at worker startup before any activities run.

Usage:
    # At worker startup
    await initialize_executor_backend()

    # In activities
    backend = get_executor_backend()
    result = await backend.execute(input, role, timeout)

    # At worker shutdown
    await shutdown_executor_backend()
"""

from __future__ import annotations

from tracecat import config
from tracecat.executor.backends.base import ExecutorBackend
from tracecat.executor.schemas import (
    ExecutorBackendType,
    resolve_backend_type,
)
from tracecat.logger import logger

__all__ = [
    "ExecutorBackend",
    "ExecutorBackendType",
    "get_executor_backend",
    "initialize_executor_backend",
    "shutdown_executor_backend",
]

# Global backend instance - set once at worker startup
_backend: ExecutorBackend | None = None


def _create_backend(backend_type: ExecutorBackendType) -> ExecutorBackend:
    """Create a backend instance by type.

    Uses lazy imports to avoid circular dependencies with action_runner.
    """
    match backend_type:
        case ExecutorBackendType.POOL:
            from tracecat.executor.backends.pool import PoolBackend

            return PoolBackend()
        case ExecutorBackendType.EPHEMERAL:
            from tracecat.executor.backends.ephemeral import EphemeralBackend

            return EphemeralBackend()
        case ExecutorBackendType.DIRECT:
            from tracecat.executor.backends.direct import DirectBackend

            return DirectBackend()
        case ExecutorBackendType.TEST:
            from tracecat.executor.backends.test import TestBackend

            return TestBackend()
        case _:
            raise ValueError(f"Unknown executor backend: {backend_type!r}")


async def initialize_executor_backend() -> ExecutorBackend:
    """Initialize the executor backend at worker startup.

    Must be called once before any activities run. Not thread-safe -
    should only be called from the main worker coroutine.

    Returns:
        The initialized ExecutorBackend instance.

    Raises:
        RuntimeError: If backend is already initialized.
        ValueError: If the configured backend type is unknown.
    """
    global _backend

    if _backend is not None:
        raise RuntimeError("Executor backend already initialized")

    backend_type = resolve_backend_type()
    logger.info(
        "Initializing executor backend",
        backend_type=backend_type,
        config_value=config.TRACECAT__EXECUTOR_BACKEND,
    )

    backend = _create_backend(backend_type)
    await backend.start()
    _backend = backend

    logger.info(
        "Executor backend initialized",
        backend_type=backend_type,
        backend_class=type(_backend).__name__,
    )

    return _backend


def get_executor_backend() -> ExecutorBackend:
    """Get the executor backend.

    The backend must have been initialized via initialize_executor_backend()
    at worker startup.

    Returns:
        The ExecutorBackend instance.

    Raises:
        RuntimeError: If backend has not been initialized.
    """
    if _backend is None:
        raise RuntimeError(
            "Executor backend not initialized. "
            "Call initialize_executor_backend() at worker startup."
        )
    return _backend


async def shutdown_executor_backend() -> None:
    """Shutdown the executor backend and release resources."""
    global _backend

    if _backend is not None:
        logger.info(
            "Shutting down executor backend",
            backend_class=type(_backend).__name__,
        )
        await _backend.shutdown()
        _backend = None
