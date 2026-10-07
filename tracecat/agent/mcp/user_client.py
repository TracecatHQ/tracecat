"""User MCP client for connecting to user-defined MCP servers.

This client handles HTTP/SSE connections to user-provided MCP servers
and proxies tool calls through the trusted server.

The client connects to external MCP servers from outside the sandbox,
allowing the sandboxed runtime to access user tools via the Unix socket proxy.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from fastmcp import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.exceptions import ToolError
from mcp import McpError
from mcp.shared._httpx_utils import McpHttpClientFactory
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.agent.common.exceptions import (
    UserMCPDiscoveryAuthError,
    UserMCPDiscoveryBudgetExceededError,
    UserMCPDiscoveryError,
    UserMCPDiscoveryTimeoutError,
    UserMCPDiscoveryUnavailableError,
)
from tracecat.agent.common.types import MCPHttpServerConfig, MCPToolDefinition
from tracecat.agent.mcp.http_limits import (
    MCPResponseTooLargeError,
    create_bounded_mcp_http_client,
)
from tracecat.agent.mcp.utils import (
    flatten_mcp_content_blocks,
    is_tracecat_registry_server_name,
)
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.logger import logger

# Discovery must finish before the 120-second preparation budget per scope.
# These cover retries too; execution timeouts for tools remain independent.
MCP_SERVER_DISCOVERY_TIMEOUT_SECONDS = 20.0
MCP_DISCOVERY_TIMEOUT_SECONDS = 60.0
# Client errors that can clear on a later attempt.
_RETRYABLE_DISCOVERY_STATUS_CODES = frozenset(
    {int(httpx.codes.REQUEST_TIMEOUT), int(httpx.codes.TOO_MANY_REQUESTS)}
)


@dataclass(frozen=True, slots=True)
class UserMCPDiscoveryResult:
    """Detailed user MCP discovery result."""

    definitions: dict[str, MCPToolDefinition]
    failed_servers: dict[str, str]


def _drop_forwarded_authorization(
    configured: dict[str, str] | None,
    *,
    origin_url: str,
) -> McpHttpClientFactory:
    """Build a client factory that strips fastmcp's forwarded inbound auth."""
    keeps_authorization = configured is not None and "authorization" in configured

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        merged = dict(headers or {})
        if not keeps_authorization:
            merged.pop("authorization", None)
        if timeout is None:
            timeout = httpx.Timeout(30.0, read=300.0)
        kwargs.setdefault("follow_redirects", True)
        return create_bounded_mcp_http_client(
            origin_url=origin_url,
            headers=merged,
            timeout=timeout,
            auth=auth,
            **kwargs,
        )

    return factory


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
    # Lowercasing alone only wins the merge when our credential is itself an
    # Authorization header; strip the forwarded token in every other case.
    httpx_client_factory = _drop_forwarded_authorization(headers, origin_url=url)
    if transport_type == "sse":
        return SSETransport(
            url=url,
            headers=headers,
            sse_read_timeout=timeout,
            httpx_client_factory=httpx_client_factory,
        )
    # Default to HTTP (Streamable HTTP transport)
    return StreamableHttpTransport(
        url=url,
        headers=headers,
        sse_read_timeout=timeout,
        httpx_client_factory=httpx_client_factory,
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
    """Return an exception, its cause/context links, and ExceptionGroup members.

    Failures surface differently by path: bare on tools/call, wrapped in a
    connect RuntimeError on the handshake, and nested inside an anyio
    ExceptionGroup in either case.
    """
    chain: list[BaseException] = []
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                stack.append(linked)
    return chain


def _contains_response_too_large(exc: BaseException) -> bool:
    """Return whether the byte-cap error appears anywhere in the failure."""
    return any(
        isinstance(chained, MCPResponseTooLargeError)
        for chained in _iter_exception_chain(exc)
    )


def _is_retryable_discovery_error_leaf(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code >= 500 or status_code in _RETRYABLE_DISCOVERY_STATUS_CODES
    if isinstance(exc, httpx.TransportError | httpx.TimeoutException | TimeoutError):
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


def _discovery_error_status_codes(exc: BaseException) -> set[int]:
    return {
        chained.response.status_code
        for chained in _iter_exception_chain(exc)
        if isinstance(chained, httpx.HTTPStatusError)
    }


def _typed_discovery_error(
    server_name: str,
    exc: BaseException,
) -> UserMCPDiscoveryError:
    """Map a discovery failure onto the typed error that carries its owner."""
    status_codes = _discovery_error_status_codes(exc)
    if status_codes & {
        int(httpx.codes.UNAUTHORIZED),
        int(httpx.codes.FORBIDDEN),
    }:
        return UserMCPDiscoveryAuthError(server_name)
    if any(
        isinstance(chained, TimeoutError | httpx.TimeoutException)
        for chained in _iter_exception_chain(exc)
    ):
        return UserMCPDiscoveryTimeoutError(server_name)
    if _is_retryable_discovery_error(exc):
        return UserMCPDiscoveryUnavailableError(server_name, retryable=True)
    # Oversized responses and JSON-RPC errors both come from the user's server.
    if (
        any(400 <= code < 500 for code in status_codes)
        or _contains_response_too_large(exc)
        or any(isinstance(chained, McpError) for chained in _iter_exception_chain(exc))
    ):
        return UserMCPDiscoveryUnavailableError(server_name, retryable=False)
    return UserMCPDiscoveryError(server_name)


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
        loop = asyncio.get_running_loop()
        discovery_deadline = loop.time() + MCP_DISCOVERY_TIMEOUT_SECONDS
        budget_exhausted = False

        for server_name, config in self._configs.items():
            # Never start another connection after the shared budget expires.
            if budget_exhausted or loop.time() >= discovery_deadline:
                budget_error = UserMCPDiscoveryBudgetExceededError(server_name)
                logger.error(
                    "Skipped user MCP server after the discovery budget expired",
                    server_name=server_name,
                )
                failed_servers[server_name] = type(budget_error).__name__
                if fail_on_error:
                    raise budget_error
                continue
            timeout: asyncio.Timeout | None = None
            server_deadline: float | None = None
            try:
                server_timeout = min(
                    config.get("timeout") or MCP_SERVER_DISCOVERY_TIMEOUT_SECONDS,
                    MCP_SERVER_DISCOVERY_TIMEOUT_SECONDS,
                )
                server_deadline = min(loop.time() + server_timeout, discovery_deadline)
                timeout = asyncio.timeout_at(server_deadline)
                async with timeout:
                    server_tools = await self._discover_server_tools(
                        server_name, config
                    )
                tools.update(server_tools)
            except Exception as e:
                # Timer callbacks can fire slightly before the clock reaches
                # their deadline. Once the shared timeout fired, never start
                # another server even if the next clock read is still earlier.
                if (
                    timeout is not None
                    and server_deadline == discovery_deadline
                    and timeout.expired()
                ):
                    budget_exhausted = True
                error_summary = _safe_discovery_error_summary(e)
                logger.error(
                    "Failed to discover tools from user MCP server",
                    server_name=server_name,
                    error_summary=error_summary,
                )
                failed_servers[server_name] = error_summary
                if fail_on_error:
                    raise _typed_discovery_error(server_name, e) from e

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

        async with Client(
            transport,
            # The outer discovery deadline also bounds initialization. FastMCP's
            # timer can discard TimeoutError when collapsing transport groups.
            init_timeout=0,
        ) as client:
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
        if is_tracecat_registry_server_name(parts[1]):
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
