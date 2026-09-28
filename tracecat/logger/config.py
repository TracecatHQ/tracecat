"""Process-wide Loguru configuration."""

import os
from enum import StrEnum


class LogFormat(StrEnum):
    """Supported stderr rendering formats."""

    CONSOLE = "console"
    JSON = "json"


def log_format_from_env() -> LogFormat:
    """Resolve the configured process-wide log rendering format."""
    raw_value = os.environ.get("TRACECAT__LOG_FORMAT")
    if raw_value is None or not raw_value.strip():
        return LogFormat.CONSOLE

    try:
        return LogFormat(raw_value.strip().lower())
    except ValueError as error:
        supported = ", ".join(log_format.value for log_format in LogFormat)
        raise ValueError(
            f"TRACECAT__LOG_FORMAT must be one of {supported} (got {raw_value!r})"
        ) from error
