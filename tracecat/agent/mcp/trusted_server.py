"""Trusted MCP server for Tracecat agent.

A FastMCP-based server with tools for executing both:
1. Registry actions (via execute_action_tool)
2. User MCP server tools (via execute_user_mcp_tool)

The proxy MCP server (inside nsjail) handles tool schema/explicitness for Claude.
This trusted server runs outside the sandbox with full network access.

Run with uvicorn on a Unix socket:
    uvicorn tracecat.agent.mcp.trusted_server:app --uds /var/run/tracecat/mcp.sock

All action execution uses nsjail sandboxing. To test locally, run in a
Docker container with nsjail installed (e.g., the executor image).
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from tracecat.agent.common.types import MCPServerConfig
from tracecat.agent.mcp.executor import execute_action
from tracecat.agent.mcp.user_client import UserMCPClient
from tracecat.agent.mcp.utils import normalize_mcp_tool_name
from tracecat.agent.tokens import verify_mcp_token
from tracecat.logger import logger
from tracecat.registry.lock.service import RegistryLockService

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
        # Resolve registry lock for this action
        # This is called from the proxy MCP server inside nsjail, so we need
        # to resolve the lock here (unlike execute_approved_tools_activity which
        # receives the lock from the workflow)
        async with RegistryLockService.with_session() as lock_service:
            registry_lock = await lock_service.resolve_lock_with_bindings(
                {normalized_action_name}
            )

        result = await execute_action(
            normalized_action_name, args, claims, registry_lock
        )
        return json.dumps(result, default=str)
    except Exception as e:
        logger.error(
            "Action execution failed",
            action_name=normalized_action_name,
            workspace_id=str(claims.workspace_id),
            error=str(e),
        )
        return json.dumps({"error": "Action execution failed"})


@mcp.tool
async def execute_user_mcp_tool(
    server_name: str,
    tool_name: str,
    args: dict[str, Any],
    auth_token: str,
) -> str:
    """Execute a tool on a user-defined MCP server.

    User MCP servers are configured in the agent config and their credentials
    are stored in the JWT claims. This tool proxies calls from the sandboxed
    runtime to the user's MCP server.

    Args:
        server_name: Name of the user MCP server (from config).
        tool_name: Original tool name (without mcp__ prefix).
        args: Arguments to pass to the tool.
        auth_token: JWT token containing user MCP server configs.

    Returns:
        JSON-encoded result from the tool.
    """
    try:
        claims = verify_mcp_token(auth_token)
    except ValueError as e:
        logger.warning("MCP token verification failed", error=str(e))
        return json.dumps({"error": "Authentication failed"})

    # Find the server config in claims
    server_config = None
    for cfg in claims.user_mcp_servers:
        if cfg.name == server_name:
            server_config = cfg
            break

    if server_config is None:
        logger.warning(
            "User MCP server not found in claims",
            server_name=server_name,
            available_servers=[s.name for s in claims.user_mcp_servers],
        )
        return json.dumps({"error": f"User MCP server '{server_name}' not authorized"})

    try:
        config_dict: MCPServerConfig = {
            "name": server_config.name,
            "url": server_config.url,
            "transport": server_config.transport,  # type: ignore[typeddict-item]
            "headers": server_config.headers,
        }

        client = UserMCPClient([config_dict])
        result = await client.call_tool(server_name, tool_name, args)

        logger.info(
            "User MCP tool executed successfully",
            server_name=server_name,
            tool_name=tool_name,
            workspace_id=str(claims.workspace_id),
        )

        return json.dumps(result, default=str)

    except Exception as e:
        logger.error(
            "User MCP tool execution failed",
            server_name=server_name,
            tool_name=tool_name,
            workspace_id=str(claims.workspace_id),
            error=str(e),
        )
        return json.dumps({"error": "Tool execution failed"})


app = mcp.http_app(path="/mcp")
