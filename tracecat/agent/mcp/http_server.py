"""Trusted HTTP MCP server for Tracecat agent.

A FastMCP-based server with a single generic `execute_action` tool.
The proxy MCP server (inside nsjail) handles tool schema/explicitness for Claude.

Run with uvicorn on a Unix socket:
    uvicorn tracecat.agent.mcp.http_server:app --uds /var/run/tracecat/mcp.sock
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from tracecat.agent.mcp.http_executor import execute_action
from tracecat.agent.tokens import verify_mcp_token

mcp = FastMCP("tracecat-actions")


@mcp.tool
async def execute_action_tool(
    action_name: str,
    args: dict[str, Any],
    auth_token: str,
) -> str:
    """Execute any Tracecat registry action.

    Args:
        action_name: The action to execute (e.g., "tools.slack.post_message")
        args: Arguments to pass to the action
        auth_token: JWT token for authentication and authorization

    Returns:
        JSON-encoded result from the action
    """
    claims = verify_mcp_token(auth_token)
    result = await execute_action(action_name, args, claims)
    return json.dumps(result, default=str)


app = mcp.http_app(path="/mcp")
