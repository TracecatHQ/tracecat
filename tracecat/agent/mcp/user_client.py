"""User MCP client for connecting to user-defined MCP servers.

This client handles HTTP/SSE connections to user-provided MCP servers
and proxies tool calls through the trusted server.

The client connects to external MCP servers from outside the sandbox,
allowing the sandboxed runtime to access user tools via the Unix socket proxy.
"""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport

from tracecat.agent.common.types import MCPServerConfig, MCPToolDefinition
from tracecat.logger import logger


def _create_transport(
    url: str,
    transport_type: Literal["http", "sse"],
    headers: dict[str, str] | None = None,
) -> StreamableHttpTransport | SSETransport:
    """Create the appropriate transport for the MCP server."""
    if transport_type == "sse":
        return SSETransport(url=url, headers=headers)
    # Default to HTTP (Streamable HTTP transport)
    return StreamableHttpTransport(url=url, headers=headers)


class UserMCPClient:
    """Client for connecting to user-defined MCP servers.

    This client is used by the trusted server to:
    1. Discover tools from user MCP servers at session start
    2. Execute tool calls by proxying to user MCP servers

    The client runs outside the sandbox (in the trusted server) and has
    full network access to reach user-provided endpoints.
    """

    def __init__(self, configs: list[MCPServerConfig]):
        """Initialize with user MCP server configurations.

        Args:
            configs: List of user MCP server configurations.

        """
        self._configs = {cfg["name"]: cfg for cfg in configs}

    async def discover_tools(self) -> dict[str, MCPToolDefinition]:
        """Connect to all configured servers and discover their tools.

        Returns:
            Dict mapping tool names (mcp__{server_name}__{tool_name}) to definitions.

        """
        tools: dict[str, MCPToolDefinition] = {}

        for server_name, config in self._configs.items():
            try:
                server_tools = await self._discover_server_tools(server_name, config)
                tools.update(server_tools)
            except Exception as e:
                logger.error(
                    "Failed to discover tools from user MCP server",
                    server_name=server_name,
                    url=config.get("url"),
                    error=str(e),
                )
                # Continue with other servers - don't fail completely

        logger.info(
            "Discovered user MCP tools",
            server_count=len(self._configs),
            tool_count=len(tools),
            tools=list(tools.keys()),
        )

        return tools

    async def _discover_server_tools(
        self,
        server_name: str,
        config: MCPServerConfig,
    ) -> dict[str, MCPToolDefinition]:
        """Discover tools from a single MCP server.

        Args:
            server_name: Name of the server for tool prefixing.
            config: Server configuration.

        Returns:
            Dict mapping prefixed tool names to definitions.

        """
        url = config["url"]
        transport_type: Literal["http", "sse"] = config.get("transport", "http")
        headers = config.get("headers")

        transport = _create_transport(url, transport_type, headers)
        tools: dict[str, MCPToolDefinition] = {}

        async with Client(transport) as client:
            # List tools from the server
            server_tools = await client.list_tools()

            for tool in server_tools:
                # Create prefixed tool name: mcp__{server_name}__{tool_name}
                prefixed_name = f"mcp__{server_name}__{tool.name}"

                # Convert MCP tool schema to our format
                tools[prefixed_name] = MCPToolDefinition(
                    name=prefixed_name,
                    description=tool.description or f"Tool from {server_name}",
                    parameters_json_schema=tool.inputSchema or {},
                )

                logger.debug(
                    "Discovered user MCP tool",
                    server_name=server_name,
                    tool_name=tool.name,
                    prefixed_name=prefixed_name,
                )

        return tools

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> Any:
        """Execute a tool on a user MCP server.

        Args:
            server_name: Name of the MCP server (from config).
            tool_name: Original tool name (without mcp__ prefix).
            args: Tool arguments.

        Returns:
            Tool execution result.

        Raises:
            ValueError: If server_name is not configured.
            Exception: If tool call fails.

        """
        if server_name not in self._configs:
            raise ValueError(f"Unknown user MCP server: {server_name}")

        config = self._configs[server_name]
        url = config["url"]
        transport_type: Literal["http", "sse"] = config.get("transport", "http")
        headers = config.get("headers")

        transport = _create_transport(url, transport_type, headers)

        logger.info(
            "Calling user MCP tool",
            server_name=server_name,
            tool_name=tool_name,
        )

        async with Client(transport) as client:
            result = await client.call_tool(tool_name, args)

            # Extract result from CallToolResult
            if result.content and len(result.content) > 0:
                first_block = result.content[0]
                # TextContent has a .text attribute
                return getattr(first_block, "text", str(first_block))

            return ""

    @staticmethod
    def parse_user_mcp_tool_name(tool_name: str) -> tuple[str, str] | None:
        """Parse a user MCP tool name into (server_name, tool_name).

        User MCP tools follow the pattern: mcp__{server_name}__{tool_name}

        Args:
            tool_name: Full tool name to parse.

        Returns:
            Tuple of (server_name, original_tool_name), or None if not a user MCP tool.

        """
        tool_name = tool_name.strip()
        # Legacy or wrapped registry names are not user-defined MCP tools.
        if tool_name.startswith("mcp.tracecat-registry."):
            return None

        # Skip tracecat-registry tools (handled separately)
        if tool_name.startswith("mcp__tracecat-registry__"):
            return None

        # Canonical user MCP format:
        #   mcp.{server_name}.{tool_name}
        if tool_name.startswith("mcp."):
            canonical_name = tool_name.removeprefix("mcp.")
            # Split on the first separator so tool names can contain dots.
            parts = canonical_name.split(".", 1)
            if len(parts) == 2 and parts[0] and parts[1]:
                return (parts[0], parts[1])

        # Check for user MCP pattern
        if not tool_name.startswith("mcp__"):
            return None

        parts = tool_name.split("__", 2)
        if len(parts) < 3:
            return None

        # parts[0] = "mcp", parts[1] = server_name, parts[2] = tool_name
        return (parts[1], parts[2])


async def discover_user_mcp_tools(
    configs: list[MCPServerConfig],
) -> dict[str, MCPToolDefinition]:
    """Discover tools from all configured user MCP servers.

    This is a convenience function for use in the executor activity.

    Args:
        configs: List of user MCP server configurations.

    Returns:
        Dict mapping prefixed tool names to their definitions.

    """
    if not configs:
        return {}

    client = UserMCPClient(configs)
    return await client.discover_tools()


async def call_user_mcp_tool(
    configs: list[MCPServerConfig],
    server_name: str,
    tool_name: str,
    args: dict[str, Any],
) -> Any:
    """Execute a tool on a user MCP server.

    This is a convenience function for use in the trusted server.

    Args:
        configs: List of user MCP server configurations.
        server_name: Name of the target server.
        tool_name: Original tool name (without prefix).
        args: Tool arguments.

    Returns:
        Tool execution result.

    """
    client = UserMCPClient(configs)
    return await client.call_tool(server_name, tool_name, args)
