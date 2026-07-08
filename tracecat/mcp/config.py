"""MCP server configuration."""

from __future__ import annotations

import os

TRACECAT_MCP__HOST: str = os.environ.get("TRACECAT_MCP__HOST", "0.0.0.0")
"""Host to bind the MCP HTTP server to."""

TRACECAT_MCP__PORT: int = int(os.environ.get("TRACECAT_MCP__PORT", "8099"))
"""Port for the MCP HTTP server."""

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
    os.environ.get("TRACECAT_MCP__MAX_INPUT_SIZE_BYTES") or 4 * 1024 * 1024
)
"""Maximum size in bytes for any single string argument to a tool call (default 4MB)."""

TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS: int = int(
    os.environ.get("TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS") or "300"
)
"""Expiry time in seconds for MCP staged file transfer URLs (default 5 minutes)."""

TRACECAT_MCP__OAUTH_CLIENT_TTL_SECONDS: int = int(
    os.environ.get("TRACECAT_MCP__OAUTH_CLIENT_TTL_SECONDS") or 0
)
"""TTL (seconds) for stored DCR clients; 0 = disabled (stored indefinitely).

When >0, registered clients expire from the store after this idle window
(sliding: refreshed on each successful get_client), bounding unbounded Redis
growth from clients that register a fresh DCR client per run (e.g. OpenCode).
Any client with a live refresh token keeps getting touched via get_client on
authorize/token/refresh, so only truly idle clients expire."""

TRACECAT_MCP__STARTUP_MAX_ATTEMPTS: int = int(
    os.environ.get("TRACECAT_MCP__STARTUP_MAX_ATTEMPTS", "3")
)
"""Maximum MCP server startup attempts before failing hard."""

TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS: float = float(
    os.environ.get("TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS", "2")
)
"""Seconds to wait between MCP startup retries."""
