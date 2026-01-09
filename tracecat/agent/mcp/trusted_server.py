"""Trusted MCP server for Tracecat agent.

A FastMCP-based server with a single generic `execute_action` tool.
The proxy MCP server (inside nsjail) handles tool schema/explicitness for Claude.

Run with uvicorn on a Unix socket:
    uvicorn tracecat.agent.mcp.trusted_server:app --uds /var/run/tracecat/mcp.sock

All action execution uses nsjail sandboxing. To test locally, run in a
Docker container with nsjail installed (e.g., the executor image).
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from tracecat.agent.mcp.executor import execute_action
from tracecat.agent.mcp.utils import normalize_mcp_tool_name
from tracecat.agent.tokens import verify_mcp_token
from tracecat.logger import logger

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
    try:
        claims = verify_mcp_token(auth_token)
    except ValueError as e:
        logger.warning("MCP token verification failed", error=str(e))
        return json.dumps({"error": "Authentication failed"})

    normalized_action_name = normalize_mcp_tool_name(action_name)

    try:
        result = await execute_action(normalized_action_name, args, claims)
        return json.dumps(result, default=str)
    except Exception as e:
        logger.error(
            "Action execution failed",
            action_name=normalized_action_name,
            workspace_id=str(claims.workspace_id),
            error=str(e),
        )
        return json.dumps({"error": "Action execution failed"})


app = mcp.http_app(path="/mcp")
