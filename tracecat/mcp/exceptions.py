"""MCP-specific exception types."""

from __future__ import annotations


class MCPNonRetryableStartupError(RuntimeError):
    """Raised when MCP startup fails due to invalid static configuration."""
