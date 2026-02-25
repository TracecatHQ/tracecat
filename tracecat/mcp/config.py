"""MCP server configuration."""

from __future__ import annotations

import os

TRACECAT_MCP__HOST: str = os.environ.get("TRACECAT_MCP__HOST", "127.0.0.1")
"""Host to bind the MCP HTTP server to."""

TRACECAT_MCP__PORT: int = int(os.environ.get("TRACECAT_MCP__PORT", "8099"))
"""Port for the MCP HTTP server."""

TRACECAT_MCP__BASE_URL: str = (
    os.environ.get("TRACECAT_MCP__BASE_URL", "").strip().rstrip("/")
    or f"http://localhost:{TRACECAT_MCP__PORT}"
)
"""Public URL where the MCP server is accessible.

Defaults to http://localhost:<TRACECAT_MCP__PORT> for local development.
"""

TRACECAT_MCP__RATE_LIMIT_RPS: float = float(
    os.environ.get("TRACECAT_MCP__RATE_LIMIT_RPS", "2.0")
)
"""Sustained requests per second per user (token bucket refill rate)."""

TRACECAT_MCP__RATE_LIMIT_BURST: int = int(
    os.environ.get("TRACECAT_MCP__RATE_LIMIT_BURST", "10")
)
"""Burst capacity for per-user rate limiting."""

TRACECAT_MCP__TOOL_TIMEOUT_SECONDS: int = int(
    os.environ.get("TRACECAT_MCP__TOOL_TIMEOUT_SECONDS", "120")
)
"""Maximum execution time in seconds for a single tool call."""

TRACECAT_MCP__MAX_INPUT_SIZE_BYTES: int = int(
    os.environ.get("TRACECAT_MCP__MAX_INPUT_SIZE_BYTES", "524288")
)
"""Maximum size in bytes for any single string argument to a tool call (default 512KB)."""

TRACECAT_MCP__STARTUP_MAX_ATTEMPTS: int = int(
    os.environ.get("TRACECAT_MCP__STARTUP_MAX_ATTEMPTS", "3")
)
"""Maximum MCP server startup attempts before failing hard."""

TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS: float = float(
    os.environ.get("TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS", "2")
)
"""Seconds to wait between MCP startup retries."""
