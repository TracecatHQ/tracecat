"""Internal tools that installed plugins register through an entry point.

A plugin owns its tools' handlers and decides which sessions get them. The
trusted MCP server runs a granted tool like a builder tool: the token must
allow it, and the handler takes its context only from the token's claims.
"""

from __future__ import annotations

import asyncio
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
    INTERNAL_TOOL_PREFIX,
    InternalToolHandler,
)
from tracecat.auth.types import Role
from tracecat.logger import logger

INTERNAL_TOOL_PLUGIN_ENTRY_POINT_GROUP = "tracecat.agent_internal_tools"
# A grant reads the database; a stuck read must not hold the turn's tool compile.
PLUGIN_GRANT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True, slots=True)
class PluginInternalTool:
    """One internal tool a plugin serves."""

    definition: MCPToolDefinition
    handler: InternalToolHandler
    requires_approval: bool
    """Default approval policy; an agent's explicit `tool_approvals` rule wins."""
    title: str
    """Display name in the agent builder."""

    @property
    def name(self) -> str:
        return self.definition.name


@dataclass(frozen=True, slots=True)
class PluginToolGroup:
    """Tools a plugin grants an agent through one provider connection."""

    provider: str
    """Provider id, such as `slack`."""
    title: str
    """Provider display name, such as `Slack`."""
    label: str
    """Which connection, such as `as @Tracecat`."""
    tools: tuple[str, ...]
    supersedes: tuple[str, ...] = ()
    """Registry action namespaces the agent stops using while connected."""


@dataclass(frozen=True, slots=True)
class SessionPluginTools:
    """What one plugin gives a session's turn."""

    tool_names: tuple[str, ...] = ()
    """Names of this plugin's tools the session gets."""
    superseded_namespaces: tuple[str, ...] = ()
    """Registry action namespaces the session's agent stops using while connected."""


@dataclass(frozen=True, slots=True)
class SessionPluginGrant:
    """Every plugin's tools and superseded namespaces for a session's turn."""

    tools: tuple[PluginInternalTool, ...] = ()
    superseded_namespaces: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AgentPluginTools:
    """A plugin tool group with its tools resolved."""

    provider: str
    title: str
    label: str
    tools: tuple[PluginInternalTool, ...]
    supersedes: tuple[str, ...]


class InternalToolPlugin(Protocol):
    """Registers internal tools and grants them per session.

    `tools` is read once per process. `session_tools` runs once per scope compile and
    `agent_tool_groups` per builder read; both run under the caller's role and
    must not block the event loop. A session's superseded namespaces must match
    the `supersedes` of its agent's groups.
    """

    def tools(self) -> Sequence[PluginInternalTool]:
        """Every tool this plugin serves."""
        ...

    async def session_tools(
        self, role: Role, session_id: uuid.UUID
    ) -> SessionPluginTools:
        """The session's tools and superseded namespaces; empty for none."""
        ...

    async def agent_tool_groups(
        self, role: Role, preset_id: uuid.UUID
    ) -> Sequence[PluginToolGroup]:
        """Groups for each connection the agent has; empty when it has none."""
        ...


@dataclass(frozen=True, slots=True)
class _LoadedPlugins:
    # Each plugin with its tool names, read once at load.
    plugins: tuple[tuple[InternalToolPlugin, frozenset[str]], ...]
    tools: Mapping[str, PluginInternalTool]


@lru_cache(maxsize=1)
def _loaded_plugins() -> _LoadedPlugins:
    """Build each installed plugin from its zero-argument entry point.

    A plugin that fails to load or names a tool badly is skipped, so one broken
    plugin never fails every turn; its tools are simply absent.
    """
    installed = sorted(
        entry_points(group=INTERNAL_TOOL_PLUGIN_ENTRY_POINT_GROUP),
        key=lambda ep: ep.name,
    )
    plugins: list[tuple[InternalToolPlugin, frozenset[str]]] = []
    tools: dict[str, PluginInternalTool] = {}
    for entry_point in installed:
        try:
            plugin: InternalToolPlugin = entry_point.load()()
            own = _validated_tools(plugin, taken=tools.keys())
        except Exception as e:
            logger.error(
                "Internal tool plugin failed to load; skipping it",
                entry_point=entry_point.name,
                error_type=type(e).__name__,
                error=str(e),
            )
            continue
        plugins.append((plugin, frozenset(own)))
        tools.update(own)
    return _LoadedPlugins(plugins=tuple(plugins), tools=MappingProxyType(tools))


def _validated_tools(
    plugin: InternalToolPlugin, *, taken: Collection[str]
) -> dict[str, PluginInternalTool]:
    """The plugin's tools by name.

    Raises:
        ValueError: If a name lacks the internal prefix, holds `__`, or is taken twice.
    """
    own: dict[str, PluginInternalTool] = {}
    for tool in plugin.tools():
        if not tool.name.startswith(INTERNAL_TOOL_PREFIX):
            raise ValueError(
                f"Plugin tool {tool.name!r} must start with {INTERNAL_TOOL_PREFIX!r}"
            )
        # MCP names swap `.` for `__`; a name holding `__` would not round-trip.
        if "__" in tool.name:
            raise ValueError(f"Plugin tool {tool.name!r} must not contain '__'")
        if (
            tool.name in own
            or tool.name in taken
            or tool.name in INTERNAL_TOOL_HANDLERS
        ):
            raise ValueError(f"Internal tool {tool.name!r} is registered twice")
        own[tool.name] = tool
    return own


def plugin_internal_tools() -> Mapping[str, PluginInternalTool]:
    """Every loaded plugin's tool by name."""
    return _loaded_plugins().tools


def plugin_internal_tool_handler(name: str) -> InternalToolHandler | None:
    if (tool := plugin_internal_tools().get(name)) is None:
        return None
    return tool.handler


def plugin_internal_tool_definitions() -> dict[str, MCPToolDefinition]:
    return {name: tool.definition for name, tool in plugin_internal_tools().items()}


async def session_plugin_grant(role: Role, session_id: uuid.UUID) -> SessionPluginGrant:
    """Every plugin's answer for a session's turn, one call per plugin.

    A plugin that fails to answer gives nothing: its tools are omitted and its
    namespaces stay usable, rather than failing the turn.
    """
    loaded = _loaded_plugins()
    tools: list[PluginInternalTool] = []
    namespaces: set[str] = set()
    for plugin, own in loaded.plugins:
        try:
            answered = await asyncio.wait_for(
                plugin.session_tools(role, session_id),
                timeout=PLUGIN_GRANT_TIMEOUT_SECONDS,
            )
            names = frozenset(answered.tool_names)
            superseded = _checked_namespaces(answered.superseded_namespaces)
        except Exception as e:
            logger.warning(
                "Internal tool plugin failed to grant tools; omitting them",
                plugin=type(plugin).__name__,
                session_id=str(session_id),
                error_type=type(e).__name__,
            )
            continue
        if foreign := sorted(map(str, names - own)):
            logger.warning(
                "Internal tool plugin granted tools it does not serve; ignoring them",
                plugin=type(plugin).__name__,
                tools=foreign,
            )
        tools.extend(loaded.tools[name] for name in names & own)
        namespaces.update(superseded)
    return SessionPluginGrant(
        tools=tuple(sorted(tools, key=lambda tool: tool.name)),
        superseded_namespaces=frozenset(namespaces),
    )


def is_superseded(action: str, namespaces: Collection[str]) -> bool:
    return any(action == ns or action.startswith(f"{ns}.") for ns in namespaces)


async def agent_plugin_tools(
    role: Role, preset_id: uuid.UUID
) -> list[AgentPluginTools]:
    """The plugin tool groups an agent's connections grant, for its builder.

    A plugin that fails to answer contributes no groups.
    """
    loaded = _loaded_plugins()
    groups: list[AgentPluginTools] = []
    for plugin, own in loaded.plugins:
        try:
            answered = await asyncio.wait_for(
                plugin.agent_tool_groups(role, preset_id),
                timeout=PLUGIN_GRANT_TIMEOUT_SECONDS,
            )
            resolved = [_resolved_group(group, own, loaded.tools) for group in answered]
        except Exception as e:
            logger.warning(
                "Internal tool plugin failed to describe an agent's tools; omitting them",
                plugin=type(plugin).__name__,
                preset_id=str(preset_id),
                error_type=type(e).__name__,
                error=str(e),
            )
            continue
        groups.extend(resolved)
    return sorted(groups, key=lambda group: (group.title, group.label))


def _resolved_group(
    group: PluginToolGroup,
    own: frozenset[str],
    tools: Mapping[str, PluginInternalTool],
) -> AgentPluginTools:
    """Raises `ValueError` for a foreign tool or a namespace that is not a registry one."""
    if foreign := sorted(set(group.tools) - own):
        raise ValueError(f"Group names tools its plugin does not serve: {foreign}")
    return AgentPluginTools(
        provider=group.provider,
        title=group.title,
        label=group.label,
        tools=tuple(tools[name] for name in sorted(set(group.tools))),
        supersedes=tuple(sorted(_checked_namespaces(group.supersedes))),
    )


def _checked_namespaces(namespaces: Collection[str]) -> frozenset[str]:
    """Raises `ValueError` for a namespace that is not a registry one."""
    for namespace in namespaces:
        if not namespace or f"{namespace}.".startswith(INTERNAL_TOOL_PREFIX):
            raise ValueError(f"Cannot supersede namespace {namespace!r}")
    return frozenset(namespaces)
