"""MCP server configuration."""

from __future__ import annotations

import os
from enum import StrEnum

from tracecat.config import TRACECAT__PUBLIC_APP_URL


class MCPAuthMode(StrEnum):
    OIDC = "oidc"
    NONE = "none"


def _parse_auth_mode(raw: str) -> MCPAuthMode:
    if not raw.strip():
        raise ValueError("TRACECAT_MCP__AUTH_METHODS must include at least one method")
    normalized = raw.strip().lower()
    try:
        return MCPAuthMode(normalized)
    except ValueError as exc:
        raise ValueError(
            "TRACECAT_MCP__AUTH_METHODS must be one of: "
            + ", ".join(mode.value for mode in MCPAuthMode)
        ) from exc


TRACECAT_MCP__HOST: str = os.environ.get("TRACECAT_MCP__HOST", "0.0.0.0")
"""Host to bind the MCP HTTP server to."""

TRACECAT_MCP__PORT: int = int(os.environ.get("TRACECAT_MCP__PORT", "8099"))
"""Port for the MCP HTTP server."""

TRACECAT_MCP__BASE_URL: str = os.environ.get(
    "TRACECAT_MCP__BASE_URL", ""
).strip().rstrip("/") or TRACECAT__PUBLIC_APP_URL.rstrip("/")
"""Public URL where the MCP server is accessible.

This should be the public root URL for the MCP OAuth/discovery endpoints.
FastMCP derives the protected resource path (for example `/mcp`) separately.
Defaults to `TRACECAT__PUBLIC_APP_URL` for local development and cluster setups.
"""

_AUTH_METHODS_ENV = os.environ.get("TRACECAT_MCP__AUTH_METHODS")

TRACECAT_MCP__AUTH_MODE: MCPAuthMode = _parse_auth_mode(
    "oidc" if _AUTH_METHODS_ENV is None else _AUTH_METHODS_ENV
)
"""Enabled MCP auth mode.

Defaults to OIDC-only. The only unsafe auth mode supported on this branch is
`none`, which allows unauthenticated access and should only be used for local
development.
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

TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS: int = int(
    os.environ.get("TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS") or "300"
)
"""Expiry time in seconds for MCP staged file transfer URLs (default 5 minutes)."""

TRACECAT_MCP__STARTUP_MAX_ATTEMPTS: int = int(
    os.environ.get("TRACECAT_MCP__STARTUP_MAX_ATTEMPTS", "3")
)
"""Maximum MCP server startup attempts before failing hard."""

TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS: float = float(
    os.environ.get("TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS", "2")
)
"""Seconds to wait between MCP startup retries."""
