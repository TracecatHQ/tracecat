"""User MCP client for connecting to user-defined MCP servers.

This client handles HTTP/SSE connections to user-provided MCP servers
and proxies tool calls through the trusted server.

The client connects to external MCP servers from outside the sandbox,
allowing the sandboxed runtime to access user tools via the Unix socket proxy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx
from fastmcp import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.exceptions import ToolError
from mcp import McpError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.agent.common.types import MCPHttpServerConfig, MCPToolDefinition
from tracecat.agent.mcp.http_limits import (
    MCPResponseTooLargeError,
    create_bounded_mcp_http_client,
)
from tracecat.agent.mcp.utils import (
    LEGACY_REGISTRY_MCP_SERVER_NAME,
    REGISTRY_MCP_SERVER_NAME,
    flatten_mcp_content_blocks,
)
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.logger import logger


@dataclass(frozen=True, slots=True)
class UserMCPDiscoveryResult:
    """Detailed user MCP discovery result."""

    definitions: dict[str, MCPToolDefinition]
    failed_servers: dict[str, str]


def _create_transport(
    url: str,
    transport_type: Literal["http", "sse"],
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
) -> StreamableHttpTransport | SSETransport:
    """Create the appropriate transport for the MCP server."""
    # FastMCP forwards inbound authorization as lowercase from request context.
    # Normalize configured headers so outbound auth overrides it instead of
    # producing duplicate Authorization headers with different casing.
    if headers is not None:
        headers = {name.lower(): value for name, value in headers.items()}
    if transport_type == "sse":
        return SSETransport(
            url=url,
            headers=headers,
            sse_read_timeout=timeout,
            httpx_client_factory=create_bounded_mcp_http_client,
        )
    # Default to HTTP (Streamable HTTP transport)
    return StreamableHttpTransport(
        url=url,
        headers=headers,
        sse_read_timeout=timeout,
        httpx_client_factory=create_bounded_mcp_http_client,
    )


async def list_remote_mcp_tools(
    config: MCPHttpServerConfig,
) -> list[MCPToolSummary]:
    """Connect to a remote HTTP/SSE MCP server and list its tools.

    Raises:
        Exception: If the server is unreachable or the MCP handshake fails.
    """
    transport = _create_transport(
        config["url"],
        config.get("transport", "http"),
        config.get("headers"),
        config.get("timeout"),
    )
    async with Client(transport) as client:
        server_tools = await client.list_tools()
    return [
        MCPToolSummary(name=tool.name, description=tool.description)
        for tool in server_tools
    ]


def _iter_exception_chain(exc: BaseException) -> list[BaseException]:
    """Return an exception and its explicit cause/context chain."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _contains_response_too_large(exc: BaseException) -> bool:
    """Walk cause/context and ExceptionGroup members for the byte-cap error.

    The cap raise surfaces differently by path: bare on tools/call, wrapped in
    a connect RuntimeError on the handshake, and nested inside an anyio
    ExceptionGroup in either case.
    """
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, MCPResponseTooLargeError):
            return True
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                stack.append(linked)
    return False


def _is_retryable_discovery_error_leaf(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    if isinstance(exc, httpx.TransportError | httpx.TimeoutException):
        return True
    if isinstance(exc, McpError):
        return exc.error.code == int(httpx.codes.REQUEST_TIMEOUT)
    return False


def _is_retryable_discovery_error(exc: BaseException) -> bool:
    """Return true for transient connect/list failures only."""
    return any(
        _is_retryable_discovery_error_leaf(chained)
        for chained in _iter_exception_chain(exc)
    )


def _safe_discovery_error_summary(exc: BaseException) -> str:
    """Summarize a discovery error without response bodies or URLs."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{type(exc).__name__}(status_code={exc.response.status_code})"
    if isinstance(exc, McpError):
        return f"{type(exc).__name__}(code={exc.error.code})"
    if exc.__cause__ is not None:
        return (
            f"{type(exc).__name__}"
            f"(cause={_safe_discovery_error_summary(exc.__cause__)})"
        )
    return type(exc).__name__


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

    async def discover_tools(
        self,
        *,
        fail_on_error: bool = False,
    ) -> dict[str, MCPToolDefinition]:
        """Connect to all configured servers and discover their tools.

        Returns:
            Dict mapping tool names (mcp__{server_name}__{tool_name}) to definitions.

        """
        result = await self.discover_tools_detailed(fail_on_error=fail_on_error)
        return result.definitions

    async def discover_tools_detailed(
        self,
        *,
        fail_on_error: bool = False,
    ) -> UserMCPDiscoveryResult:
        """Connect to all configured servers and report per-server failures."""
        tools: dict[str, MCPToolDefinition] = {}
        failed_servers: dict[str, str] = {}

        for server_name, config in self._configs.items():
            try:
                server_tools = await self._discover_server_tools(server_name, config)
                tools.update(server_tools)
            except Exception as e:
                error_summary = _safe_discovery_error_summary(e)
                logger.error(
                    "Failed to discover tools from user MCP server",
                    server_name=server_name,
                    error_summary=error_summary,
                )
                failed_servers[server_name] = error_summary
                if fail_on_error:
                    raise RuntimeError(
                        f"Failed to discover tools from user MCP server '{server_name}'"
                    ) from e

        logger.info(
            "Discovered user MCP tools",
            server_count=len(self._configs),
            tool_count=len(tools),
            tools=list(tools.keys()),
            failed_servers=list(failed_servers),
        )

        return UserMCPDiscoveryResult(definitions=tools, failed_servers=failed_servers)

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
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_retryable_discovery_error),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
            reraise=True,
        ):
            with attempt:
                return await self._discover_server_tools_once(server_name, config)
        raise RuntimeError("MCP server discovery retry loop exited unexpectedly")

    async def _discover_server_tools_once(
        self,
        server_name: str,
        config: MCPHttpServerConfig,
    ) -> dict[str, MCPToolDefinition]:
        """Discover tools from a single MCP server without retries."""
        url = config["url"]
        transport_type: Literal["http", "sse"] = config.get("transport", "http")
        headers = config.get("headers")
        timeout = config.get("timeout")

        transport = _create_transport(url, transport_type, headers, timeout)
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
        timeout = config.get("timeout")

        transport = _create_transport(url, transport_type, headers, timeout)

        logger.info(
            "Calling user MCP tool",
            server_name=server_name,
            tool_name=tool_name,
        )

        try:
            async with Client(transport) as client:
                result = await client.call_tool(tool_name, args)

                # Flatten every block: file bodies arrive as EmbeddedResource,
                # not as the leading TextContent status line.
                return flatten_mcp_content_blocks(result.content)
        except Exception as e:
            # Catch Exception, not BaseException: the cap error surfaces bare, in
            # an ExceptionGroup, or wrapped in RuntimeError (all Exception). A
            # CancelledError carrying the cap error in __context__ must propagate.
            if _contains_response_too_large(e):
                raise ToolError("MCP server response exceeded 16 MiB limit") from e
            raise

    @staticmethod
    def _is_tracecat_registry_server_name(server_name: str) -> bool:
        return (
            server_name in {REGISTRY_MCP_SERVER_NAME, LEGACY_REGISTRY_MCP_SERVER_NAME}
            or server_name.startswith(f"{REGISTRY_MCP_SERVER_NAME}-")
            or server_name.startswith(f"{LEGACY_REGISTRY_MCP_SERVER_NAME}_")
        )

    @staticmethod
    def parse_user_mcp_tool_name(tool_name: str) -> tuple[str, str] | None:
        """Parse a user MCP tool name into (server_name, tool_name).

        User MCP tools follow the pattern: mcp__{server_name}__{tool_name}

        Args:
            tool_name: Full tool name to parse.

        Returns:
            Tuple of (server_name, original_tool_name), or None if not a user MCP tool.

        """
        # Check for user MCP pattern
        if not tool_name.startswith("mcp__"):
            return None

        parts = tool_name.split("__", 2)
        if len(parts) < 3:
            return None
        if UserMCPClient._is_tracecat_registry_server_name(parts[1]):
            return None

        # parts[0] = "mcp", parts[1] = server_name, parts[2] = tool_name
        return (parts[1], parts[2])


async def discover_user_mcp_tools(
    configs: list[MCPHttpServerConfig],
    *,
    fail_on_error: bool = False,
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
    return await client.discover_tools(fail_on_error=fail_on_error)


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
