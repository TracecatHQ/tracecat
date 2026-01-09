"""Proxy stdin MCP server for Claude agent.

Creates a per-job MCP server that exposes only configured tools and
forwards execution requests to the trusted MCP server via Unix socket.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.agent.mcp.utils import action_name_to_mcp_tool_name
from tracecat.logger import logger

# Default socket path for nsjail mode (mounted at /var/run/tracecat).
# In direct mode, orchestrator sets TRACECAT__MCP_SOCKET_PATH env var.
_DEFAULT_MCP_SOCKET_PATH = "/var/run/tracecat/mcp.sock"


def _get_mcp_socket_path() -> str:
    """Get the MCP socket path from env or use hardcoded default."""
    return os.environ.get("TRACECAT__MCP_SOCKET_PATH", _DEFAULT_MCP_SOCKET_PATH)


class _UDSClientFactory:
    """Factory for creating httpx AsyncClient with Unix Domain Socket transport.

    Implements the McpHttpClientFactory protocol expected by StreamableHttpTransport.

    Note: FastMCP passes additional kwargs beyond the protocol (e.g., follow_redirects),
    so we accept **kwargs to handle them gracefully.
    """

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path

    def __call__(
        self,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        """Create an AsyncClient configured for UDS transport."""
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=self._socket_path),
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kwargs,
        )


def _create_uds_transport(socket_path: str) -> StreamableHttpTransport:
    """Create a StreamableHttpTransport that connects via Unix socket."""
    return StreamableHttpTransport(
        url="http://localhost/mcp",
        httpx_client_factory=_UDSClientFactory(socket_path),
    )


async def create_proxy_mcp_server(
    allowed_actions: dict[str, MCPToolDefinition],
    auth_token: str,
) -> McpSdkServerConfig:
    """Create proxy MCP server from pre-provided tool definitions.

    The proxy server exposes only the tools in allowed_actions and forwards
    all execution requests to the trusted MCP server via Unix socket.

    Args:
        allowed_actions: Dict mapping action names to their definitions.
        auth_token: JWT token for authenticating with trusted server.

    Returns:
        McpSdkServerConfig ready for use with Claude agent.
    """
    tools: list[SdkMcpTool] = []

    for action_name, defn in allowed_actions.items():
        # Create handler with captured variables
        def make_handler(
            captured_action_name: str,
            captured_auth_token: str,
        ) -> Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]:
            async def _handler(args: dict[str, Any]) -> dict[str, Any]:
                logger.info(
                    "Proxy forwarding tool call",
                    action_name=captured_action_name,
                )

                try:
                    # Connect via HTTP over Unix socket
                    transport = _create_uds_transport(_get_mcp_socket_path())
                    async with Client(transport) as client:
                        call_result = await client.call_tool(
                            "execute_action_tool",
                            {
                                "action_name": captured_action_name,
                                "args": args,
                                "auth_token": captured_auth_token,
                            },
                        )

                    # Extract text from CallToolResult content blocks
                    if call_result.content and len(call_result.content) > 0:
                        first_block = call_result.content[0]
                        # TextContent has a .text attribute
                        result_text = getattr(first_block, "text", str(first_block))
                    else:
                        result_text = ""

                    return {"content": [{"type": "text", "text": result_text}]}

                except Exception as e:
                    logger.error(
                        "Proxy request failed",
                        action_name=captured_action_name,
                        error=str(e),
                    )
                    return {
                        "content": [
                            {"type": "text", "text": "Error: Proxy request failed"}
                        ],
                        "isError": True,
                    }

            return _handler

        # Convert action name to MCP-compatible tool name
        tool_name = action_name_to_mcp_tool_name(action_name)

        handler = make_handler(action_name, auth_token)
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
        name="tracecat-registry",
        version="1.0.0",
        tools=tools,
    )
