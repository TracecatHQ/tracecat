"""Configuration for tracecat-registry package."""

import os
from ipaddress import IPv4Network, IPv6Network, ip_network
from typing import Any

from pydantic_core import to_jsonable_python as _to_jsonable_python


def to_jsonable_python(value: Any) -> Any:
    """Convert a value to a JSONable Python object.

    Drop nulls and use fallback for unknown values.
    """

    def fallback(x: Any) -> Any:
        """Fallback for unknown values."""
        return None

    return _to_jsonable_python(value, fallback=fallback, exclude_none=True)


# API list/search limits (mirror tracecat/config.py where relevant)
TRACECAT__LIMIT_MIN = 1
TRACECAT__LIMIT_DEFAULT = 20
TRACECAT__LIMIT_CURSOR_MAX = 200
TRACECAT__LIMIT_TABLE_DOWNLOAD_MAX = 1000

# File upload limits
TRACECAT__MAX_FILE_SIZE_BYTES = int(
    os.environ.get("TRACECAT__MAX_FILE_SIZE_BYTES", 20 * 1024 * 1024)  # Default 20MB
)
TRACECAT__MAX_UPLOAD_FILES_COUNT = int(
    os.environ.get("TRACECAT__MAX_UPLOAD_FILES_COUNT", 5)
)
TRACECAT__MAX_AGGREGATE_UPLOAD_SIZE_BYTES = int(
    os.environ.get("TRACECAT__MAX_AGGREGATE_UPLOAD_SIZE_BYTES", 100 * 1024 * 1024)
)


# Outbound egress policy for caller-supplied browser targets
def _env_networks(name: str) -> tuple[IPv4Network | IPv6Network, ...]:
    """Parse a comma-separated environment variable into validated IP networks.

    Mirrors `env_networks` in `tracecat/config.py`. This package is a dependency
    of `tracecat`, so it cannot import that module.
    """
    raw_value = os.environ.get(name, "")
    if not raw_value.strip():
        return ()
    networks: list[IPv4Network | IPv6Network] = []
    for value in raw_value.split(","):
        stripped = value.strip()
        if not stripped:
            continue
        try:
            networks.append(ip_network(stripped))
        except ValueError as exc:
            raise ValueError(
                f"{name} contains an invalid network: {stripped!r}"
            ) from exc
    return tuple(networks)


TRACECAT__OUTBOUND_ALLOWED_PRIVATE_CIDRS = _env_networks(
    "TRACECAT__OUTBOUND_ALLOWED_PRIVATE_CIDRS"
)
"""Operator-only exceptions to the browser-target egress policy.

Empty by default, and read from the same variable the API's outbound HTTP policy
uses, so one deployment-level setting covers both. Configure exact IPs or narrow
CIDRs only for intentional private targets, and set it in the process or
container environment rather than in workspace or action inputs.
"""

# S3 concurrency limit
TRACECAT__S3_CONCURRENCY_LIMIT = int(
    os.environ.get("TRACECAT__S3_CONCURRENCY_LIMIT", 10)
)

# DuckDB extension directory. Set in the Docker images to the directory that
# preinstalled DuckDB extensions are copied into. Left unset in local/dev/test
# environments, where DuckDB falls back to its default autoinstall behaviour.
TRACECAT__DUCKDB_EXTENSION_DIRECTORY = os.environ.get(
    "TRACECAT__DUCKDB_EXTENSION_DIRECTORY"
)

# Database connection validation (used to prevent connecting to internal DB)
TRACECAT__DB_URI = os.environ.get(
    "TRACECAT__DB_URI",
    "postgresql+psycopg://postgres:postgres@postgres_db:5432/postgres",
)
TRACECAT__DB_ENDPOINT = os.environ.get("TRACECAT__DB_ENDPOINT")
TRACECAT__DB_PORT = os.environ.get("TRACECAT__DB_PORT")
TRACECAT__AGENT_MAX_RETRIES = int(os.environ.get("TRACECAT__AGENT_MAX_RETRIES", 20))

TRACECAT__AGENT_MAX_TOOL_CALLS = int(
    os.environ.get("TRACECAT__AGENT_MAX_TOOL_CALLS", 40)
)
"""The maximum number of tool calls that can be made per agent run."""

TRACECAT__AGENT_MAX_REQUESTS = int(os.environ.get("TRACECAT__AGENT_MAX_REQUESTS", 120))
"""The maximum number of requests that can be made per agent run."""
