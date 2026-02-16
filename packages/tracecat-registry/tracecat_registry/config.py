"""Configuration for tracecat-registry package."""

import os
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


# Maximum number of rows that can be returned from client-facing queries
MAX_ROWS_CLIENT_POSTGRES = int(
    os.environ.get("TRACECAT__MAX_ROWS_CLIENT_POSTGRES", 1000)
)

# Maximum case page size for list/search endpoints
MAX_CASES_CLIENT_POSTGRES = min(
    MAX_ROWS_CLIENT_POSTGRES,
    int(os.environ.get("TRACECAT__LIMIT_CASES_MAX", 200)),
)

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

# S3 concurrency limit
TRACECAT__S3_CONCURRENCY_LIMIT = int(
    os.environ.get("TRACECAT__S3_CONCURRENCY_LIMIT", 10)
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


class _FeatureFlags:
    """Feature flags checked directly from env to avoid heavy tracecat imports.

    Attributes are set in __init__ to allow patching in tests.
    """

    def __init__(self) -> None:
        _flags = os.environ.get("TRACECAT__FEATURE_FLAGS", "")
        self.case_tasks: bool = "case-tasks" in _flags
        """Enable case tasks (enterprise feature)."""
        self.case_durations: bool = "case-durations" in _flags
        """Enable case durations (enterprise feature)."""
        self.agent_presets: bool = "agent-presets" in _flags
        """Enable agent presets UDFs."""
        self.ai_ranking: bool = "ai-ranking" in _flags
        """Enable AI ranking UDFs."""


flags = _FeatureFlags()
