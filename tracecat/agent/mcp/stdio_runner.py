"""Pluggable runner for stdio MCP servers.

Tracecat calls a stdio MCP server in two places: the probe workflow lists its
tools when an integration is saved, and the trusted MCP server calls a tool for
a gateway-routed turn. Both go through one ``StdioMCPRunner``. The built-in
runner probes under nsjail and does not run tool calls; an installed package
can replace it through the ``tracecat.stdio_mcp_runner`` entry point group.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Protocol, runtime_checkable

from mcp.types import CallToolResult

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.mcp.stdio_probe_types import StdioMCPProbeResult
from tracecat.agent.mcp.utils import MCP_TOOL_NAME_RE
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.logger import logger

STDIO_MCP_RUNNER_ENTRY_POINT_GROUP = "tracecat.stdio_mcp_runner"
DEFAULT_STDIO_MCP_RUNNER = "nsjail"


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


class StdioMCPRunnerUnsupportedError(Exception):
    """The installed runner cannot run this operation."""


@runtime_checkable
class StdioMCPRunner(Protocol):
    """Runs stdio MCP servers on behalf of Tracecat.

    ``env`` is the server's resolved process environment, credentials
    included. Implementations must only hand it to the server process.
    """

    async def list_tools(
        self, server: StdioMCPServer, env: dict[str, str] | None
    ) -> StdioMCPProbeResult:
        """Start the server, list its tools, and stop it."""
        ...

    async def call_tool(
        self,
        server: StdioMCPServer,
        env: dict[str, str] | None,
        tool: str,
        args: dict[str, Any],
    ) -> CallToolResult:
        """Start the server, call one tool, and stop it.

        Raises:
            StdioMCPRunnerUnsupportedError: The runner cannot call tools.
        """
        ...


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


@lru_cache(maxsize=1)
def get_stdio_mcp_runner() -> StdioMCPRunner:
    """Load the installed runner once, or the built-in one if none is installed."""
    # The built-in runner imports the nsjail sandbox, so load it lazily too.
    installed = list(entry_points(group=STDIO_MCP_RUNNER_ENTRY_POINT_GROUP))
    if len(installed) > 1:
        names = ", ".join(sorted(entry.name for entry in installed))
        raise ValueError(f"Multiple stdio MCP runners are installed: {names}")
    entry = (
        installed[0]
        if installed
        else EntryPoint(
            name=DEFAULT_STDIO_MCP_RUNNER,
            value="tracecat.agent.mcp.stdio_probe:NsjailStdioMCPRunner",
            group=STDIO_MCP_RUNNER_ENTRY_POINT_GROUP,
        )
    )
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", entry.name):
        raise ValueError(f"Invalid stdio MCP runner identifier: {entry.name}")
    factory = entry.load()
    if not callable(factory):
        raise TypeError(f"Stdio MCP runner {entry.name} must expose a factory")
    runner = factory()
    if not isinstance(runner, StdioMCPRunner):
        raise TypeError(f"Invalid stdio MCP runner contract: {entry.name}")
    return runner
