"""Internal tools that installed plugins register through an entry point.

A plugin owns its tools' handlers and decides which sessions get them. The
trusted MCP server runs a granted tool like a builder tool: the token must
allow it, and the handler takes its context only from the token's claims.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import entry_points
from types import MappingProxyType
from typing import Protocol

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.mcp.internal_tools import (
    INTERNAL_TOOL_HANDLERS,
    InternalToolHandler,
)
from tracecat.auth.types import Role
from tracecat.logger import logger

INTERNAL_TOOL_PLUGIN_ENTRY_POINT_GROUP = "tracecat.agent_internal_tools"
INTERNAL_TOOL_PREFIX = "internal."


@dataclass(frozen=True, slots=True)
class PluginInternalTool:
    """One internal tool a plugin serves."""

    definition: MCPToolDefinition
    handler: InternalToolHandler
    requires_approval: bool
    """Default approval policy; an agent's explicit `tool_approvals` rule wins."""

    @property
    def name(self) -> str:
        return self.definition.name


class InternalToolPlugin(Protocol):
    """Registers internal tools and grants them per session.

    `tools` is read once per process. `granted_tools` runs at each tool
    compile under the turn's role and must not block the event loop.
    """

    def tools(self) -> Sequence[PluginInternalTool]:
        """Every tool this plugin serves."""
        ...

    async def granted_tools(self, role: Role, session_id: uuid.UUID) -> Collection[str]:
        """Names of this plugin's tools the session gets; empty for none."""
        ...


@lru_cache(maxsize=1)
def _plugins() -> tuple[InternalToolPlugin, ...]:
    """Build each installed plugin from its zero-argument entry point."""
    installed = sorted(
        entry_points(group=INTERNAL_TOOL_PLUGIN_ENTRY_POINT_GROUP),
        key=lambda ep: ep.name,
    )
    return tuple(entry_point.load()() for entry_point in installed)


@lru_cache(maxsize=1)
def plugin_internal_tools() -> Mapping[str, PluginInternalTool]:
    """Every plugin tool by name.

    Raises:
        ValueError: If a name lacks the internal prefix or is taken twice.
    """
    tools: dict[str, PluginInternalTool] = {}
    for plugin in _plugins():
        for tool in plugin.tools():
            if not tool.name.startswith(INTERNAL_TOOL_PREFIX):
                raise ValueError(
                    f"Plugin tool {tool.name!r} must start with {INTERNAL_TOOL_PREFIX!r}"
                )
            if tool.name in tools or tool.name in INTERNAL_TOOL_HANDLERS:
                raise ValueError(f"Internal tool {tool.name!r} is registered twice")
            tools[tool.name] = tool
    return MappingProxyType(tools)


def plugin_internal_tool_handler(name: str) -> InternalToolHandler | None:
    if (tool := plugin_internal_tools().get(name)) is None:
        return None
    return tool.handler


def plugin_internal_tool_definitions() -> dict[str, MCPToolDefinition]:
    return {name: tool.definition for name, tool in plugin_internal_tools().items()}


async def granted_plugin_internal_tools(
    role: Role, session_id: uuid.UUID
) -> list[PluginInternalTool]:
    """The plugin tools a session gets, in name order.

    A plugin that fails to answer grants nothing: its tools are omitted
    rather than failing the turn.
    """
    tools = plugin_internal_tools()
    granted: list[PluginInternalTool] = []
    for plugin in _plugins():
        try:
            names = await plugin.granted_tools(role, session_id)
        except Exception as e:
            logger.warning(
                "Internal tool plugin failed to grant tools; omitting them",
                plugin=type(plugin).__name__,
                session_id=str(session_id),
                error_type=type(e).__name__,
            )
            continue
        own = {tool.name for tool in plugin.tools()}
        granted.extend(tools[name] for name in names if name in own)
    return sorted(granted, key=lambda tool: tool.name)
