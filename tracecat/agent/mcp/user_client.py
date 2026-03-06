"""User MCP client for connecting to user-defined MCP servers.

This client handles HTTP/SSE connections to user-provided MCP servers
and proxies tool calls through the trusted server.

The client connects to external MCP servers from outside the sandbox,
allowing the sandboxed runtime to access user tools via the Unix socket proxy.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

import mcp.types as mcp_types
import orjson
from fastmcp import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from mcp import McpError

from tracecat.agent.common.types import MCPHttpServerConfig, MCPToolDefinition
from tracecat.logger import logger


class MCPServerCatalogArtifact(TypedDict):
    """Normalized MCP artifact payload for persisted discovery."""

    artifact_type: Literal["tool", "resource", "prompt"]
    artifact_ref: str
    display_name: str | None
    description: str | None
    input_schema: dict[str, Any] | None
    metadata: dict[str, Any] | None
    raw_payload: dict[str, Any]
    content_hash: str


@dataclass(frozen=True, slots=True)
class MCPServerCatalog:
    """Normalized catalog for a single remote MCP server."""

    server_name: str
    tools: tuple[MCPServerCatalogArtifact, ...]
    resources: tuple[MCPServerCatalogArtifact, ...]
    prompts: tuple[MCPServerCatalogArtifact, ...]

    @property
    def artifacts(self) -> tuple[MCPServerCatalogArtifact, ...]:
        """Return a flattened view across all MCP artifact types."""
        return self.tools + self.resources + self.prompts

    def to_tool_definitions(self) -> dict[str, MCPToolDefinition]:
        """Convert catalog tools into the existing bootstrap tool definition map."""
        tools: dict[str, MCPToolDefinition] = {}
        for tool in self.tools:
            tool_name = tool["artifact_ref"]
            prefixed_name = f"mcp__{self.server_name}__{tool_name}"
            tools[prefixed_name] = MCPToolDefinition(
                name=prefixed_name,
                description=tool["description"] or f"Tool from {self.server_name}",
                parameters_json_schema=tool["input_schema"] or {},
            )
        return tools


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


def _model_to_payload(model: Any) -> dict[str, Any]:
    """Convert an MCP model into a stable JSON payload."""
    if not hasattr(model, "model_dump"):
        raise TypeError(f"Unsupported MCP artifact type: {type(model).__name__}")
    payload = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    if not isinstance(payload, dict):
        raise TypeError(f"Unexpected MCP artifact payload: {type(payload).__name__}")
    return payload


def _content_hash(payload: dict[str, Any]) -> str:
    """Compute a stable content hash for a normalized MCP artifact."""
    canonical = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(canonical).hexdigest()


def _compact_metadata(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Drop empty metadata fields to keep persisted payloads compact."""
    compact = {key: value for key, value in metadata.items() if value is not None}
    return compact or None


def _prompt_input_schema(
    arguments: list[mcp_types.PromptArgument] | None,
) -> dict[str, Any]:
    """Build a simple JSON schema for prompt arguments."""
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for argument in arguments or []:
        property_schema: dict[str, Any] = {"type": "string"}
        if argument.description is not None:
            property_schema["description"] = argument.description
        properties[argument.name] = property_schema
        if argument.required:
            required.append(argument.name)

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        input_schema["required"] = required
    return input_schema


def _normalize_tool(tool: mcp_types.Tool) -> MCPServerCatalogArtifact:
    """Normalize a remote MCP tool into the persisted discovery shape."""
    raw_payload = _model_to_payload(tool)
    display_name = tool.title or (tool.annotations.title if tool.annotations else None)
    metadata = _compact_metadata(
        {
            "icons": [
                icon.model_dump(mode="json", exclude_none=True) for icon in tool.icons
            ]
            if tool.icons
            else None,
            "annotations": tool.annotations.model_dump(mode="json", exclude_none=True)
            if tool.annotations
            else None,
            "meta": tool.meta,
            "output_schema": tool.outputSchema,
            "execution": tool.execution.model_dump(mode="json", exclude_none=True)
            if tool.execution
            else None,
        }
    )
    return {
        "artifact_type": "tool",
        "artifact_ref": tool.name,
        "display_name": display_name or tool.name,
        "description": tool.description,
        "input_schema": tool.inputSchema,
        "metadata": metadata,
        "raw_payload": raw_payload,
        "content_hash": _content_hash(raw_payload),
    }


def _normalize_resource(resource: mcp_types.Resource) -> MCPServerCatalogArtifact:
    """Normalize a remote MCP resource into the persisted discovery shape."""
    raw_payload = _model_to_payload(resource)
    metadata = _compact_metadata(
        {
            "name": resource.name,
            "mime_type": resource.mimeType,
            "size": resource.size,
            "icons": [
                icon.model_dump(mode="json", exclude_none=True)
                for icon in resource.icons
            ]
            if resource.icons
            else None,
            "annotations": resource.annotations.model_dump(
                mode="json", exclude_none=True
            )
            if resource.annotations
            else None,
            "meta": resource.meta,
        }
    )
    display_name = resource.title or resource.name or str(resource.uri)
    return {
        "artifact_type": "resource",
        "artifact_ref": str(resource.uri),
        "display_name": display_name,
        "description": resource.description,
        "input_schema": None,
        "metadata": metadata,
        "raw_payload": raw_payload,
        "content_hash": _content_hash(raw_payload),
    }


def _normalize_prompt(prompt: mcp_types.Prompt) -> MCPServerCatalogArtifact:
    """Normalize a remote MCP prompt into the persisted discovery shape."""
    raw_payload = _model_to_payload(prompt)
    input_schema = _prompt_input_schema(prompt.arguments)
    metadata = _compact_metadata(
        {
            "arguments": [
                argument.model_dump(mode="json", exclude_none=True)
                for argument in prompt.arguments or []
            ],
            "icons": [
                icon.model_dump(mode="json", exclude_none=True) for icon in prompt.icons
            ]
            if prompt.icons
            else None,
            "meta": prompt.meta,
        }
    )
    return {
        "artifact_type": "prompt",
        "artifact_ref": prompt.name,
        "display_name": prompt.title or prompt.name,
        "description": prompt.description,
        "input_schema": input_schema,
        "metadata": metadata,
        "raw_payload": raw_payload,
        "content_hash": _content_hash(raw_payload),
    }


def _is_unsupported_optional_capability(exc: McpError) -> bool:
    """Return whether a list_resources/list_prompts error means unsupported."""
    message = exc.error.message.lower()
    return exc.error.code == mcp_types.METHOD_NOT_FOUND or (
        "method not found" in message or "not supported" in message
    )


async def _list_optional_capability[T](
    *,
    server_name: str,
    capability_name: str,
    list_fn: Callable[[], Awaitable[list[T]]],
) -> list[T]:
    """List an optional MCP capability, downgrading unsupported methods to empty."""
    try:
        return await list_fn()
    except McpError as exc:
        if not _is_unsupported_optional_capability(exc):
            raise
        logger.info(
            "Remote MCP capability not supported",
            server_name=server_name,
            capability=capability_name,
            error_code=exc.error.code,
            error_message=exc.error.message,
        )
        return []


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
        tools = tuple(_normalize_tool(tool) for tool in await client.list_tools())
        resources = tuple(
            _normalize_resource(resource)
            for resource in await _list_optional_capability(
                server_name=server_name,
                capability_name="resources",
                list_fn=client.list_resources,
            )
        )
        prompts = tuple(
            _normalize_prompt(prompt)
            for prompt in await _list_optional_capability(
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
