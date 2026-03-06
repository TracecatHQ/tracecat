"""User MCP client for connecting to user-defined MCP servers.

This client handles HTTP/SSE connections to user-provided MCP servers
and proxies tool calls through the trusted server.

The client connects to external MCP servers from outside the sandbox,
allowing the sandboxed runtime to access user tools via the Unix socket proxy.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from fastmcp import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from mcp.types import (
    BlobResourceContents,
    GetPromptResult,
    TextResourceContents,
)
from yarl import URL

from tracecat.agent.common.types import MCPHttpServerConfig, MCPToolDefinition
from tracecat.agent.mcp.catalog import (
    MCPServerCatalog,
    list_optional_capability,
    normalize_prompt,
    normalize_resource,
    normalize_tool,
)
from tracecat.logger import logger


def _create_transport(
    url: str,
    transport_type: Literal["http", "sse"],
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
) -> StreamableHttpTransport | SSETransport:
    """Create the appropriate transport for the MCP server."""
    if transport_type == "sse":
        return SSETransport(url=url, headers=headers, sse_read_timeout=timeout)
    # Default to HTTP (Streamable HTTP transport)
    return StreamableHttpTransport(url=url, headers=headers, sse_read_timeout=timeout)


async def discover_mcp_server_catalog(
    config: MCPHttpServerConfig,
) -> MCPServerCatalog:
    """Discover normalized tools/resources/prompts for a single MCP server."""
    server_name = config["name"]
    url = config["url"]
    transport_type: Literal["http", "sse"] = config.get("transport", "http")
    headers = config.get("headers")
    timeout = config.get("timeout")

    transport = _create_transport(url, transport_type, headers, timeout)

    async with Client(transport) as client:
        tools = tuple(normalize_tool(tool) for tool in await client.list_tools())
        resources = tuple(
            normalize_resource(resource)
            for resource in await list_optional_capability(
                server_name=server_name,
                capability_name="resources",
                list_fn=client.list_resources,
            )
        )
        prompts = tuple(
            normalize_prompt(prompt)
            for prompt in await list_optional_capability(
                server_name=server_name,
                capability_name="prompts",
                list_fn=client.list_prompts,
            )
        )

    logger.debug(
        "Discovered remote MCP server catalog",
        server_name=server_name,
        tool_count=len(tools),
        resource_count=len(resources),
        prompt_count=len(prompts),
    )

    return MCPServerCatalog(
        server_name=server_name,
        tools=tools,
        resources=resources,
        prompts=prompts,
    )


def infer_transport_type(url: str) -> Literal["http", "sse"]:
    """Infer transport type from a persisted MCP server URL."""
    path = URL(url).path.lower()
    if path.endswith("/sse") or "/sse/" in path:
        return "sse"
    return "http"


class UserMCPClient:
    """Client for connecting to user-defined MCP servers.

    This client is used by the trusted server to:
    1. Discover tools from user MCP servers at session start
    2. Execute tool calls by proxying to user MCP servers

    The client runs outside the sandbox (in the trusted server) and has
    full network access to reach user-provided endpoints.
    """

    def __init__(self, configs: list[MCPHttpServerConfig]):
        """Initialize with user MCP server configurations.

        Args:
            configs: List of user MCP server configurations.

        """
        self._configs = {cfg["name"]: cfg for cfg in configs}

    def _get_config(self, server_name: str) -> MCPHttpServerConfig:
        if server_name not in self._configs:
            raise ValueError(f"Unknown user MCP server: {server_name}")
        return self._configs[server_name]

    def _get_transport(
        self, server_name: str
    ) -> StreamableHttpTransport | SSETransport:
        config = self._get_config(server_name)
        url = config["url"]
        transport_type: Literal["http", "sse"] = config.get("transport", "http")
        headers = config.get("headers")
        timeout = config.get("timeout")
        return _create_transport(url, transport_type, headers, timeout)

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
                    error_type=type(e).__name__,
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
        config: MCPHttpServerConfig,
    ) -> dict[str, MCPToolDefinition]:
        """Discover tools from a single MCP server.

        Args:
            server_name: Name of the server for tool prefixing.
            config: Server configuration.

        Returns:
            Dict mapping prefixed tool names to definitions.

        """
        catalog = await discover_mcp_server_catalog(config)
        tools = catalog.to_tool_definitions()
        for tool_name in tools:
            logger.debug(
                "Discovered user MCP tool",
                server_name=server_name,
                tool_name=tool_name.removeprefix(f"mcp__{server_name}__"),
                prefixed_name=tool_name,
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
        transport = self._get_transport(server_name)

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

    async def call_tool_result(
        self,
        server_name: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> Any:
        """Execute a tool and return the full MCP result payload."""
        transport = self._get_transport(server_name)
        logger.info(
            "Calling user MCP tool with full result",
            server_name=server_name,
            tool_name=tool_name,
        )
        async with Client(transport) as client:
            return cast(Any, await client.call_tool(tool_name, args))

    async def read_resource(
        self,
        server_name: str,
        resource_uri: str,
    ) -> list[TextResourceContents | BlobResourceContents]:
        """Read a resource from a user MCP server."""
        transport = self._get_transport(server_name)
        logger.info(
            "Reading user MCP resource",
            server_name=server_name,
            resource_uri=resource_uri,
        )
        async with Client(transport) as client:
            return cast(
                list[TextResourceContents | BlobResourceContents],
                await client.read_resource(resource_uri),
            )

    async def get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        args: dict[str, Any] | None = None,
    ) -> GetPromptResult:
        """Fetch a prompt from a user MCP server."""
        transport = self._get_transport(server_name)
        logger.info(
            "Fetching user MCP prompt",
            server_name=server_name,
            prompt_name=prompt_name,
        )
        async with Client(transport) as client:
            return cast(GetPromptResult, await client.get_prompt(prompt_name, args))

    @staticmethod
    def parse_user_mcp_tool_name(tool_name: str) -> tuple[str, str] | None:
        """Parse a user MCP tool name into (server_name, tool_name).

        User MCP tools follow the pattern: mcp__{server_name}__{tool_name}

        Args:
            tool_name: Full tool name to parse.

        Returns:
            Tuple of (server_name, original_tool_name), or None if not a user MCP tool.

        """
        # Skip tracecat registry-reserved prefixes (handled separately).
        # Support both alias forms.
        if tool_name.startswith("mcp__tracecat-registry__") or tool_name.startswith(
            "mcp__tracecat_registry__"
        ):
            return None

        # Check for user MCP pattern
        if not tool_name.startswith("mcp__"):
            return None

        parts = tool_name.split("__", 2)
        if len(parts) < 3:
            return None

        # parts[0] = "mcp", parts[1] = server_name, parts[2] = tool_name
        return (parts[1], parts[2])


async def discover_user_mcp_tools(
    configs: list[MCPHttpServerConfig],
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
    configs: list[MCPHttpServerConfig],
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
