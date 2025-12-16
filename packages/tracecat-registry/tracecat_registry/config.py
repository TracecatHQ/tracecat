"""Registry configuration from environment variables.

This module provides configuration values that registry actions need,
read directly from environment variables without importing tracecat.
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum

logger = logging.getLogger(__name__)


# === Feature Flags === #
class FeatureFlag(StrEnum):
    """Feature flag enum."""

    GIT_SYNC = "git-sync"
    AGENT_APPROVALS = "agent-approvals"
    AGENT_PRESETS = "agent-presets"
    CASE_DURATIONS = "case-durations"
    CASE_TASKS = "case-tasks"
    REGISTRY_SYNC_V2 = "registry-sync-v2"


def _parse_feature_flags() -> set[FeatureFlag]:
    """Parse feature flags from environment."""
    flags: set[FeatureFlag] = set()
    for flag in os.environ.get("TRACECAT__FEATURE_FLAGS", "").split(","):
        if not (flag_value := flag.strip()):
            continue
        try:
            flags.add(FeatureFlag(flag_value))
        except ValueError:
            logger.warning(
                "Ignoring unknown feature flag '%s' from TRACECAT__FEATURE_FLAGS",
                flag_value,
            )
    return flags


TRACECAT__FEATURE_FLAGS = _parse_feature_flags()
"""Set of enabled feature flags."""


def is_feature_enabled(flag: FeatureFlag | str) -> bool:
    """Check if a feature flag is enabled."""
    return flag in TRACECAT__FEATURE_FLAGS


# === API Config === #
TRACECAT__API_URL = os.environ.get("TRACECAT__API_URL", "http://localhost:8000")
"""Base URL of the Tracecat API."""

TRACECAT__EXECUTOR_URL = os.environ.get(
    "TRACECAT__EXECUTOR_URL", "http://executor:8000"
)
"""Base URL of the Tracecat executor service."""


# === File Limits === #
TRACECAT__MAX_FILE_SIZE_BYTES = int(
    os.environ.get("TRACECAT__MAX_FILE_SIZE_BYTES", 20 * 1024 * 1024)  # Default 20MB
)
"""The maximum size for file handling (e.g., uploads, downloads) in bytes."""

TRACECAT__MAX_AGGREGATE_UPLOAD_SIZE_BYTES = int(
    os.environ.get("TRACECAT__MAX_AGGREGATE_UPLOAD_SIZE_BYTES", 100 * 1024 * 1024)
)
"""The maximum size of the aggregate upload size in bytes. Defaults to 100MB."""

TRACECAT__MAX_UPLOAD_FILES_COUNT = int(
    os.environ.get("TRACECAT__MAX_UPLOAD_FILES_COUNT", 5)
)
"""The maximum number of files that can be uploaded at once. Defaults to 5."""

TRACECAT__S3_CONCURRENCY_LIMIT = int(
    os.environ.get("TRACECAT__S3_CONCURRENCY_LIMIT", 50)
)
"""Maximum number of concurrent S3 operations to prevent resource exhaustion."""

TRACECAT__MAX_ROWS_CLIENT_POSTGRES = int(
    os.environ.get("TRACECAT__MAX_ROWS_CLIENT_POSTGRES", 1000)
)
"""Maximum number of rows that can be returned from PostgreSQL client queries."""

# === DB Config (for SQL action validation only) === #
# These are used to prevent users from connecting to Tracecat's internal database.
# In the executor environment, these should be set to the blocked endpoint info
# without exposing actual credentials.
TRACECAT__DB_URI = os.environ.get("TRACECAT__DB_URI", "")
"""Internal database URI for validation (endpoint check only, not used for connections)."""

TRACECAT__DB_ENDPOINT = os.environ.get("TRACECAT__DB_ENDPOINT")
"""The endpoint of the internal database (for blocking user connections)."""

TRACECAT__DB_PORT = os.environ.get("TRACECAT__DB_PORT")
"""The port of the internal database (for blocking user connections)."""

# === Sandbox Config === #
TRACECAT__DISABLE_NSJAIL = os.environ.get(
    "TRACECAT__DISABLE_NSJAIL", "true"
).lower() in ("true", "1")
"""Disable nsjail sandbox and use safe Python executor instead."""

TRACECAT__SANDBOX_NSJAIL_PATH = os.environ.get(
    "TRACECAT__SANDBOX_NSJAIL_PATH", "/usr/local/bin/nsjail"
)
"""Path to the nsjail binary for sandbox execution."""

TRACECAT__SANDBOX_ROOTFS_PATH = os.environ.get(
    "TRACECAT__SANDBOX_ROOTFS_PATH", "/var/lib/tracecat/sandbox-rootfs"
)
"""Path to the sandbox rootfs directory containing Python 3.12 + uv."""

TRACECAT__SANDBOX_CACHE_DIR = os.environ.get(
    "TRACECAT__SANDBOX_CACHE_DIR", "/var/lib/tracecat/sandbox-cache"
)
"""Base directory for sandbox caching (packages, uv cache)."""

TRACECAT__SANDBOX_DEFAULT_TIMEOUT = int(
    os.environ.get("TRACECAT__SANDBOX_DEFAULT_TIMEOUT", "300")
)
"""Default timeout for sandbox script execution in seconds."""

TRACECAT__SANDBOX_DEFAULT_MEMORY_MB = int(
    os.environ.get("TRACECAT__SANDBOX_DEFAULT_MEMORY_MB", "2048")
)
"""Default memory limit for sandbox execution in megabytes (2 GiB)."""

TRACECAT__SANDBOX_PYPI_INDEX_URL = os.environ.get(
    "TRACECAT__SANDBOX_PYPI_INDEX_URL", "https://pypi.org/simple"
)
"""Primary PyPI index URL for package installation."""

TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS = [
    url.strip()
    for url in os.environ.get("TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS", "").split(",")
    if url.strip()
]
"""Additional PyPI index URLs (comma-separated)."""
