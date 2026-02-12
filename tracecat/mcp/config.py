"""MCP server configuration."""

from __future__ import annotations

import os

TRACECAT_MCP__BASE_URL: str = os.environ.get("TRACECAT_MCP__BASE_URL", "")
"""Public URL where the MCP server is accessible (e.g. https://mcp.yourcompany.com)."""

TRACECAT_MCP__HOST: str = os.environ.get("TRACECAT_MCP__HOST", "127.0.0.1")
"""Host to bind the MCP HTTP server to."""

TRACECAT_MCP__PORT: int = int(os.environ.get("TRACECAT_MCP__PORT", "8099"))
"""Port for the MCP HTTP server."""
