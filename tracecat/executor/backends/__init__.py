"""Executor backend implementations.

This module provides the factory function for getting the configured
executor backend, as well as exports for all backend implementations.

Usage:
    from tracecat.executor.backends import get_executor_backend

    backend = await get_executor_backend()
    result = await backend.execute(input, role, timeout)
"""

from __future__ import annotations

import asyncio

from tracecat import config
from tracecat.executor.backends.base import ExecutorBackend
from tracecat.executor.schemas import (
    ExecutorBackendType,
    _resolve_backend_type,
    get_trust_mode,
)
from tracecat.logger import logger

__all__ = [
    "ExecutorBackend",
    "ExecutorBackendType",
    "get_executor_backend",
    "get_trust_mode",
    "shutdown_executor_backend",
]

# Global singleton backend instance
_backend: ExecutorBackend | None = None
_backend_lock = asyncio.Lock()


def _create_backend(backend_type: ExecutorBackendType) -> ExecutorBackend:
    """Create a backend instance by type.

    Uses lazy imports to avoid circular dependencies with action_runner.
    """
    match backend_type:
        case ExecutorBackendType.SANDBOXED_POOL:
            from tracecat.executor.backends.sandboxed_pool import SandboxedPoolBackend

            return SandboxedPoolBackend()
        case ExecutorBackendType.EPHEMERAL:
            from tracecat.executor.backends.ephemeral import EphemeralBackend

            return EphemeralBackend()
        case ExecutorBackendType.DIRECT:
            from tracecat.executor.backends.direct import DirectBackend

            return DirectBackend()
        case _:
            # This shouldn't be reachable due to enum validation
            raise ValueError(f"Unknown executor backend: {backend_type!r}")


async def get_executor_backend() -> ExecutorBackend:
    """Get the configured executor backend (singleton).

    The backend is lazily initialized on first call and reused for
    subsequent calls. Thread-safe via asyncio lock.

    Returns:
        The configured ExecutorBackend instance.

    Raises:
        ValueError: If the configured backend type is unknown.
    """
    global _backend

    # Fast path: backend already initialized
    if _backend is not None:
        return _backend

    async with _backend_lock:
        # Double-check after acquiring lock
        if _backend is not None:
            return _backend

        backend_type = _resolve_backend_type()
        logger.info(
            "Initializing executor backend",
            backend_type=backend_type,
            config_value=config.TRACECAT__EXECUTOR_BACKEND,
        )

        # Create and start in local variable first to avoid corrupted state if start() fails
        backend = _create_backend(backend_type)
        await backend.start()

        # Only assign to global after successful start
        _backend = backend

        logger.info(
            "Executor backend initialized",
            backend_type=backend_type,
            backend_class=type(_backend).__name__,
        )

        return _backend


async def shutdown_executor_backend() -> None:
    """Shutdown the executor backend and release resources."""
    global _backend

    async with _backend_lock:
        if _backend is not None:
            logger.info(
                "Shutting down executor backend",
                backend_class=type(_backend).__name__,
            )
            await _backend.shutdown()
            _backend = None
