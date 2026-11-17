"""Saved stdio MCP servers, as agent backends run them.

Each agent backend runs stdio MCP servers its own way
(``AgentBackend.register_stdio_mcp_server`` and ``call_stdio_mcp_tool``).
This module holds what they share: the server a backend is handed and the
saved tool list every backend compiles its tools from.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.mcp.utils import MCP_TOOL_NAME_RE
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.logger import logger


@dataclass(frozen=True, slots=True, kw_only=True)
class StdioMCPServer:
    """A saved stdio MCP integration, without its environment.

    Built from the workspace-scoped integration row. Secrets travel separately
    as the resolved ``env`` argument and are never stored on this object.
    """

    workspace_id: uuid.UUID
    mcp_integration_id: uuid.UUID
    name: str
    """The integration slug: the server name in ``mcp__{name}__{tool}``."""
    command: str
    args: tuple[str, ...] = ()
    timeout: int | None = None
    """Per-call timeout in seconds configured on the integration."""


class StdioMCPCallUnsupportedError(Exception):
    """The backend does not run stdio MCP tool calls through Tracecat."""


def usable_stdio_mcp_tools(
    tools: Iterable[MCPToolSummary],
) -> list[MCPToolSummary]:
    """Saved tools an agent may use: enabled, still advertised, validly named."""
    usable: list[MCPToolSummary] = []
    for tool in tools:
        if not tool.enabled or tool.status != "available":
            continue
        if not MCP_TOOL_NAME_RE.fullmatch(tool.name):
            logger.warning(
                "Skipping stdio MCP tool with unsupported name", tool_name=tool.name
            )
            continue
        usable.append(tool)
    return usable


def stdio_mcp_tool_definition(
    server_name: str, tool: MCPToolSummary
) -> MCPToolDefinition:
    """Build a saved stdio tool's definition, named ``mcp__{server}__{tool}``."""
    name = f"mcp__{server_name}__{tool.name}"
    return MCPToolDefinition(
        name=name,
        description=tool.description or f"Tool from {server_name}",
        # Tools verified before schemas were saved accept any arguments.
        parameters_json_schema=tool.input_schema
        or {"type": "object", "additionalProperties": True},
    )
