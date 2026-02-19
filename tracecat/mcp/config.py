"""MCP server configuration."""

from __future__ import annotations

import os
from enum import StrEnum


class MCPAuthMode(StrEnum):
    OIDC_INTERACTIVE = "oidc_interactive"
    OAUTH_CLIENT_CREDENTIALS_JWT = "oauth_client_credentials_jwt"
    OAUTH_CLIENT_CREDENTIALS_INTROSPECTION = "oauth_client_credentials_introspection"


def _env_mcp_auth_mode(name: str, default: MCPAuthMode) -> MCPAuthMode:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip()
    try:
        return MCPAuthMode(value)
    except ValueError as exc:
        allowed_values = ", ".join(mode.value for mode in MCPAuthMode)
        raise ValueError(
            f"Invalid value for {name}: {value!r}. Expected one of: {allowed_values}"
        ) from exc


TRACECAT_MCP__AUTH_MODE: MCPAuthMode = _env_mcp_auth_mode(
    "TRACECAT_MCP__AUTH_MODE",
    MCPAuthMode.OIDC_INTERACTIVE,
)
"""Authentication mode for the external MCP server."""

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

TRACECAT_MCP__AUTHORIZATION_SERVER_URL: str | None = os.environ.get(
    "TRACECAT_MCP__AUTHORIZATION_SERVER_URL"
)
"""URL of the external OAuth authorization server.

Required for oauth_client_credentials_jwt and oauth_client_credentials_introspection
auth modes. Used to advertise the authorization server in RFC 9728 protected resource
metadata so MCP clients know where to obtain tokens.
"""
