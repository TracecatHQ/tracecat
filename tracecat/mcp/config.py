"""MCP server configuration."""

from __future__ import annotations

import os

from tracecat.config import TRACECAT__PUBLIC_APP_URL


def _parse_auth_methods(raw: str) -> frozenset[str]:
    if not raw.strip():
        return frozenset({"oidc"})
    methods = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not methods:
        raise ValueError("TRACECAT_MCP__AUTH_METHODS must include at least one method")
    valid_methods = {"oidc", "api_key", "none"}
    invalid_methods = sorted(methods - valid_methods)
    if invalid_methods:
        raise ValueError(
            "TRACECAT_MCP__AUTH_METHODS contains invalid values: "
            + ", ".join(invalid_methods)
        )
    return frozenset(methods)


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

TRACECAT_MCP__AUTH_METHODS: frozenset[str] = _parse_auth_methods(
    os.environ.get("TRACECAT_MCP__AUTH_METHODS") or "oidc"
)
"""Enabled MCP auth methods.

Defaults to OIDC-only. Additional methods such as API key and authless mode
must be explicitly opted into.

Valid options:
- `oidc`: existing interactive OIDC flow
- `api_key`: accept `Authorization: Bearer <TRACECAT_MCP__API_KEY>`
- `none`: accept unauthenticated requests and treat the caller as a
  superuser-equivalent MCP identity; highly unsafe and not recommended
"""

TRACECAT_MCP__API_KEY: str | None = os.environ.get("TRACECAT_MCP__API_KEY") or None
"""Dedicated bearer token for MCP `api_key` auth.

This is intentionally separate from `TRACECAT__SERVICE_KEY` so MCP bypass access
can be scoped and rotated independently from other internal service credentials.
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
