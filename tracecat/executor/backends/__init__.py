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
from typing import TYPE_CHECKING

from tracecat import config
from tracecat.executor.backend import ExecutorBackend
from tracecat.executor.backends.direct import DirectBackend
from tracecat.executor.backends.ephemeral import EphemeralBackend
from tracecat.executor.backends.sandboxed_pool import SandboxedPoolBackend
from tracecat.logger import logger

if TYPE_CHECKING:
    pass

__all__ = [
    "DirectBackend",
    "EphemeralBackend",
    "ExecutorBackend",
    "SandboxedPoolBackend",
    "get_executor_backend",
    "shutdown_executor_backend",
]

# Global singleton backend instance
_backend: ExecutorBackend | None = None
_backend_lock = asyncio.Lock()


def _is_nsjail_available() -> bool:
    """Check if nsjail sandbox is available."""
    from pathlib import Path

    nsjail_path = Path(config.TRACECAT__SANDBOX_NSJAIL_PATH)
    rootfs_path = Path(config.TRACECAT__SANDBOX_ROOTFS_PATH)

    return nsjail_path.exists() and rootfs_path.exists()


def _resolve_backend_type() -> str:
    """Resolve the backend type from config, handling 'auto' mode."""
    backend_type = config.TRACECAT__EXECUTOR_BACKEND

    if backend_type == "auto":
        # Auto-select based on environment
        if config.TRACECAT__DISABLE_NSJAIL:
            logger.info(
                "Auto-selecting 'direct' backend (DISABLE_NSJAIL=true)",
            )
            return "direct"
        elif _is_nsjail_available():
            logger.info(
                "Auto-selecting 'sandboxed_pool' backend (nsjail available)",
            )
            return "sandboxed_pool"
        else:
            logger.warning(
                "Auto-selecting 'direct' backend (nsjail not available)",
            )
            return "direct"

    return backend_type


def _create_backend(backend_type: str) -> ExecutorBackend:
    """Create a backend instance by type."""
    if backend_type == "sandboxed_pool":
        return SandboxedPoolBackend()
    elif backend_type == "ephemeral":
        return EphemeralBackend()
    elif backend_type == "direct":
        return DirectBackend()
    else:
        raise ValueError(
            f"Unknown executor backend: {backend_type!r}. "
            f"Supported: 'sandboxed_pool', 'ephemeral', 'direct', 'auto'"
        )


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

        _backend = _create_backend(backend_type)
        await _backend.start()

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
