"""
Registry sync package for subprocess-based repository loading.

This package isolates the potentially disruptive operations (uv install,
importlib.reload, module loading) into a subprocess to prevent environment
contamination in the main API process.
"""

from tracecat.registry.sync.schemas import (
    SyncResultAdapter,
    SyncResultError,
    SyncResultSuccess,
)
from tracecat.registry.sync.subprocess import fetch_actions_from_subprocess

__all__ = [
    "SyncResultAdapter",
    "SyncResultError",
    "SyncResultSuccess",
    "fetch_actions_from_subprocess",
]
