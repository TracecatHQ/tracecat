"""Proxy stdin MCP server for Claude agent.

Creates a per-job MCP server that exposes only configured tools and
forwards execution requests to the trusted MCP server via Unix socket.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import orjson
from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool
from fastmcp import Client

from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.logger import logger


async def create_proxy_mcp_server(
    allowed_actions: dict[str, MCPToolDefinition],
    auth_token: str,
    trusted_socket_path: str,
) -> McpSdkServerConfig:
    """Create proxy MCP server from pre-provided tool definitions.

    The proxy server exposes only the tools in allowed_actions and forwards
    all execution requests to the trusted MCP server via Unix socket.

    Args:
        allowed_actions: Dict mapping action names to their definitions.
        auth_token: JWT token for authenticating with trusted server.
        trusted_socket_path: Path to the trusted MCP server's Unix socket.

    Returns:
        McpSdkServerConfig ready for use with Claude agent.
    """
    tools: list[SdkMcpTool] = []

    for action_name, defn in allowed_actions.items():
        # Convert action name to MCP-compatible tool name
        tool_name = action_name.replace(".", "__")

        # Create handler with captured variables
        def make_handler(
            captured_action_name: str,
            captured_auth_token: str,
            captured_socket_path: str,
        ) -> Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]:
            async def _handler(args: dict[str, Any]) -> dict[str, Any]:
                logger.info(
                    "Proxy forwarding tool call",
                    action_name=captured_action_name,
                )

                try:
                    async with Client(captured_socket_path) as client:
                        result = await client.call_tool(
                            "execute_action_tool",
                            {
                                "action_name": captured_action_name,
                                "args": args,
                                "auth_token": captured_auth_token,
                            },
                        )

                    # Format result for MCP response
                    if isinstance(result, (dict, list)):
                        result_text = orjson.dumps(result, default=str).decode()
                    else:
                        result_text = str(result)

                    return {"content": [{"type": "text", "text": result_text}]}

                except Exception as e:
                    logger.error(
                        "Proxy request failed",
                        action_name=captured_action_name,
                        error=str(e),
                    )
                    return {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    }

            return _handler

        handler = make_handler(action_name, auth_token, trusted_socket_path)
        decorated = tool(tool_name, defn.description, defn.parameters_json_schema)(
            handler
        )
        tools.append(decorated)

        logger.debug(
            "Created proxy tool",
            action_name=action_name,
            tool_name=tool_name,
        )

    logger.info(
        "Created proxy MCP server",
        tool_count=len(tools),
    )

    return create_sdk_mcp_server(
        name="tracecat-proxy",
        version="1.0.0",
        tools=tools,
    )
