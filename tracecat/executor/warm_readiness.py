"""Sentinel-file based readiness gate for executor warm cache.

After the executor finishes its startup warmup attempt (success, timeout, or
error), it writes a marker file to disk. Kubernetes readiness probes check for
this file to decide when the executor pod is ready to receive traffic.

The file is cleared on every startup so stale markers from previous runs
don't leak across container restarts.
"""

from __future__ import annotations

import os
from pathlib import Path

from tracecat import config


def get_warm_ready_file() -> Path:
    """Return the configured readiness marker file path."""
    return Path(config.TRACECAT__EXECUTOR_WARM_READY_FILE)


def clear_warm_ready_file() -> None:
    """Remove the readiness marker file if it exists."""
    ready_file = get_warm_ready_file()
    ready_file.unlink(missing_ok=True)


def mark_warm_ready() -> None:
    """Atomically create the readiness marker file.

    Uses write-to-temp + rename to avoid partial reads by concurrent probes.
    """
    ready_file = get_warm_ready_file()
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = ready_file.with_name(f"{ready_file.name}.{os.getpid()}.tmp")
    temp_file.write_text("ready\n", encoding="utf-8")
    temp_file.replace(ready_file)


def is_warm_ready() -> bool:
    """Return True if the readiness marker file exists on disk."""
    return get_warm_ready_file().exists()
