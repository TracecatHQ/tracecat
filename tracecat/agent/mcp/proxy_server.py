"""Proxy stdin MCP server for Claude agent.

Creates a per-job MCP server that exposes only configured tools and
forwards execution requests to the trusted MCP server via Unix socket.

Handles two types of tools:
1. Registry actions (e.g., core.cases.list_cases) -> execute_action_tool
2. User MCP tools (e.g., mcp__my-server__my_tool) -> execute_user_mcp_tool
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import httpx
from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.mcp.user_client import UserMCPClient
from tracecat.agent.mcp.utils import action_name_to_mcp_tool_name
from tracecat.agent.sandbox.config import TRUSTED_MCP_SOCKET_PATH
from tracecat.logger import logger


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


def _make_tool_handler(
    trusted_tool_name: str,
    trusted_tool_args: dict[str, Any],
    auth_token: str,
    log_context: dict[str, Any],
) -> Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]:
    """Create handler that forwards tool calls to the trusted MCP server.

    Args:
        trusted_tool_name: Name of the tool on the trusted server to call.
        trusted_tool_args: Static args to pass (tool name/action identifiers).
        auth_token: JWT token for authentication.
        log_context: Additional context for logging.
    """

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        logger.info("Proxy forwarding tool call", **log_context)

        try:
            transport = _create_uds_transport(str(TRUSTED_MCP_SOCKET_PATH))
            async with Client(transport) as client:
                call_result = await client.call_tool(
                    trusted_tool_name,
                    {**trusted_tool_args, "args": args, "auth_token": auth_token},
                )

            # Check if the tool call returned an error
            if call_result.is_error:
                # Extract error message from content
                error_text = ""
                if call_result.content and len(call_result.content) > 0:
                    first_block = call_result.content[0]
                    error_text = getattr(first_block, "text", str(first_block))
                logger.error(
                    "Tool call returned error", error=error_text, **log_context
                )
                raise RuntimeError(error_text or "Tool execution failed")

            if call_result.content and len(call_result.content) > 0:
                first_block = call_result.content[0]
                result_text = getattr(first_block, "text", str(first_block))
            else:
                result_text = ""

            return {"content": [{"type": "text", "text": result_text}]}

        except Exception as e:
            logger.error("Proxy request failed", error=str(e), **log_context)
            raise

    return _handler


async def create_proxy_mcp_server(
    allowed_actions: dict[str, MCPToolDefinition],
    auth_token: str,
) -> McpSdkServerConfig:
    """Create proxy MCP server from pre-provided tool definitions.

    The proxy server exposes only the tools in allowed_actions and forwards
    execution requests to the trusted MCP server via Unix socket.

    Handles three types of tools:
    - Registry actions (e.g., core.cases.list_cases) -> execute_action_tool
    - User MCP tools (e.g., mcp__my-server__my_tool) -> execute_user_mcp_tool
    - Internal tools (e.g., internal.builder.get_preset_summary) -> execute_internal_tool

    Args:
        allowed_actions: Dict mapping action names to their definitions.
            User MCP tools use the format mcp__{server_name}__{tool_name}.
            Internal tools use the format internal.{category}.{tool_name}.
        auth_token: JWT token for authenticating with trusted server.

    Returns:
        McpSdkServerConfig ready for use with Claude agent.
    """
    tools: list[SdkMcpTool[Any]] = []

    for action_name, defn in allowed_actions.items():
        # Check if this is a user MCP tool
        parsed = UserMCPClient.parse_user_mcp_tool_name(action_name)

        if parsed:
            # User MCP tool: mcp__{server_name}__{tool_name}
            server_name, original_tool_name = parsed
            handler = _make_tool_handler(
                "execute_user_mcp_tool",
                {"server_name": server_name, "tool_name": original_tool_name},
                auth_token,
                {
                    "tool_type": "user_mcp",
                    "server_name": server_name,
                    "tool_name": original_tool_name,
                },
            )
            # Use the full prefixed name as MCP tool name (already in correct format)
            mcp_tool_name = action_name
            tool_type = "user_mcp"
        elif action_name.startswith("internal."):
            # Internal tool: internal.{category}.{tool_name}
            handler = _make_tool_handler(
                "execute_internal_tool",
                {"tool_name": action_name},
                auth_token,
                {"tool_type": "internal", "tool_name": action_name},
            )
            # Convert dots to underscores for MCP compatibility
            mcp_tool_name = action_name_to_mcp_tool_name(action_name)
            tool_type = "internal"
        else:
            # Registry action tool
            handler = _make_tool_handler(
                "execute_action_tool",
                {"action_name": action_name},
                auth_token,
                {"tool_type": "registry", "action_name": action_name},
            )
            # Convert dots to underscores for MCP compatibility
            mcp_tool_name = action_name_to_mcp_tool_name(action_name)
            tool_type = "registry"

        decorated = tool(mcp_tool_name, defn.description, defn.parameters_json_schema)(
            handler
        )
        tools.append(decorated)

        logger.debug(
            "Created proxy tool",
            action_name=action_name,
            mcp_tool_name=mcp_tool_name,
            tool_type=tool_type,
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
