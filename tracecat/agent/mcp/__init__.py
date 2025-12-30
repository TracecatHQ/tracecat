"""MCP server implementations for Tracecat agent.

This package provides two MCP servers:
1. Trusted HTTP MCP Server - Long-lived, single execute_action tool on Unix socket
2. Proxy stdin MCP Server - Per-job, explicit tools that forward to trusted server
"""

from tracecat.agent.mcp.http_executor import (
    ActionExecutionError,
    ActionNotAllowedError,
    execute_action,
)
from tracecat.agent.mcp.proxy_server import create_proxy_mcp_server
from tracecat.agent.mcp.schemas import fetch_tool_definitions
from tracecat.agent.mcp.tokens import (
    MCPTokenClaims,
    mint_mcp_token,
    verify_mcp_token,
)
from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.agent.mcp.utils import normalize_mcp_tool_name

__all__ = [
    # Types
    "MCPToolDefinition",
    "MCPTokenClaims",
    # Token functions
    "mint_mcp_token",
    "verify_mcp_token",
    # Schema utilities
    "fetch_tool_definitions",
    # Proxy server (inside nsjail)
    "create_proxy_mcp_server",
    # Executor (used by http_server)
    "execute_action",
    "ActionNotAllowedError",
    "ActionExecutionError",
    # Utilities
    "normalize_mcp_tool_name",
]
