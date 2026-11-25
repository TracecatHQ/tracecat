"""Internal tools that installed plugins register through an entry point.

A plugin owns its tools' handlers and decides which sessions get them. The
trusted MCP server runs a granted tool like a builder tool: the token must
allow it, and the handler takes its context only from the token's claims.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import entry_points
from types import MappingProxyType

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.mcp.internal_tools import (
    INTERNAL_TOOL_HANDLERS,
    INTERNAL_TOOL_PREFIX,
    InternalToolHandler,
)
from tracecat.agent.mcp.types import (
    AgentPluginToolGroups,
    AgentPluginTools,
    InternalToolPlugin,
    PluginInternalTool,
    PluginToolGroup,
    SessionPluginGrant,
)
from tracecat.auth.types import Role
from tracecat.logger import logger

INTERNAL_TOOL_PLUGIN_ENTRY_POINT_GROUP = "tracecat.agent_internal_tools"
# A grant reads the database; a stuck read must not hold the turn's tool compile.
PLUGIN_GRANT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True, slots=True)
class _LoadedPlugin:
    # Read once at load.
    name: str
    plugin: InternalToolPlugin
    tool_names: frozenset[str]
    superseded_namespaces: frozenset[str]


@dataclass(frozen=True, slots=True)
class _LoadedPlugins:
    plugins: tuple[_LoadedPlugin, ...]
    tools: Mapping[str, PluginInternalTool]
    failed: tuple[str, ...]
    """Entry point names of plugins that failed to load."""


@lru_cache(maxsize=1)
def _loaded_plugins() -> _LoadedPlugins:
    """Build each installed plugin from its zero-argument entry point.

    A plugin that fails to load, names a tool badly or declares an internal
    namespace is skipped, so one broken plugin never fails every turn; its tools
    are simply absent.
    """
    installed = sorted(
        entry_points(group=INTERNAL_TOOL_PLUGIN_ENTRY_POINT_GROUP),
        key=lambda ep: ep.name,
    )
    plugins: list[_LoadedPlugin] = []
    tools: dict[str, PluginInternalTool] = {}
    failed: list[str] = []
    for entry_point in installed:
        try:
            plugin: InternalToolPlugin = entry_point.load()()
            own = _validated_tools(plugin, taken=tools.keys())
            namespaces = _checked_namespaces(plugin.superseded_namespaces())
        except Exception as e:
            logger.error(
                "Internal tool plugin failed to load; skipping it",
                entry_point=entry_point.name,
                error_type=type(e).__name__,
                error=str(e),
            )
            failed.append(entry_point.name)
            continue
        plugins.append(
            _LoadedPlugin(
                name=entry_point.name,
                plugin=plugin,
                tool_names=frozenset(own),
                superseded_namespaces=namespaces,
            )
        )
        tools.update(own)
    return _LoadedPlugins(
        plugins=tuple(plugins), tools=MappingProxyType(tools), failed=tuple(failed)
    )


def log_loaded_internal_tool_plugins() -> None:
    """Load the installed plugins and log each one's tool count, once at startup."""
    loaded = _loaded_plugins()
    logger.info(
        "Internal tool plugins loaded",
        plugins={
            loaded_plugin.name: len(loaded_plugin.tool_names)
            for loaded_plugin in loaded.plugins
        },
        failed=list(loaded.failed),
    )


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

    A plugin that fails to answer grants no tools and supersedes every namespace
    it declares, so the turn neither fails nor falls back to the registry tools.
    """
    loaded = _loaded_plugins()
    tools: list[PluginInternalTool] = []
    namespaces: set[str] = set()
    for loaded_plugin in loaded.plugins:
        try:
            answered = await asyncio.wait_for(
                loaded_plugin.plugin.session_tools(role, session_id),
                timeout=PLUGIN_GRANT_TIMEOUT_SECONDS,
            )
            names = frozenset(answered.tool_names)
            superseded = _checked_namespaces(answered.superseded_namespaces)
            if undeclared := sorted(superseded - loaded_plugin.superseded_namespaces):
                raise ValueError(f"Supersedes undeclared namespaces: {undeclared}")
        except Exception as e:
            logger.warning(
                "Internal tool plugin failed to grant tools; omitting them and"
                " superseding its namespaces",
                plugin=loaded_plugin.name,
                session_id=str(session_id),
                error_type=type(e).__name__,
            )
            namespaces.update(loaded_plugin.superseded_namespaces)
            continue
        if foreign := sorted(map(str, names - loaded_plugin.tool_names)):
            logger.warning(
                "Internal tool plugin granted tools it does not serve; ignoring them",
                plugin=loaded_plugin.name,
                tools=foreign,
            )
        tools.extend(loaded.tools[name] for name in names & loaded_plugin.tool_names)
        namespaces.update(superseded)
    return SessionPluginGrant(
        tools=tuple(sorted(tools, key=lambda tool: tool.name)),
        superseded_namespaces=frozenset(namespaces),
    )


def is_superseded(action: str, namespaces: Collection[str]) -> bool:
    return any(action == ns or action.startswith(f"{ns}.") for ns in namespaces)


async def agent_plugin_tools(role: Role, preset_id: uuid.UUID) -> AgentPluginToolGroups:
    """The plugin tool groups an agent's connections grant, for its builder.

    A plugin that fails to load or answer contributes no groups and is named in
    `failed_plugins`.
    """
    loaded = _loaded_plugins()
    groups: list[AgentPluginTools] = []
    failed = list(loaded.failed)
    for loaded_plugin in loaded.plugins:
        try:
            answered = await asyncio.wait_for(
                loaded_plugin.plugin.agent_tool_groups(role, preset_id),
                timeout=PLUGIN_GRANT_TIMEOUT_SECONDS,
            )
            resolved = [
                _resolved_group(group, loaded_plugin, loaded.tools)
                for group in answered
            ]
        except Exception as e:
            logger.warning(
                "Internal tool plugin failed to describe an agent's tools; omitting them",
                plugin=loaded_plugin.name,
                preset_id=str(preset_id),
                error_type=type(e).__name__,
                error=str(e),
            )
            failed.append(loaded_plugin.name)
            continue
        groups.extend(resolved)
    return AgentPluginToolGroups(
        groups=tuple(sorted(groups, key=lambda group: (group.title, group.label))),
        failed_plugins=tuple(sorted(failed)),
    )


def _resolved_group(
    group: PluginToolGroup,
    loaded_plugin: _LoadedPlugin,
    tools: Mapping[str, PluginInternalTool],
) -> AgentPluginTools:
    """Raises `ValueError` for a foreign tool or an undeclared or internal namespace."""
    if foreign := sorted(set(group.tools) - loaded_plugin.tool_names):
        raise ValueError(f"Group names tools its plugin does not serve: {foreign}")
    supersedes = _checked_namespaces(group.supersedes)
    if undeclared := sorted(supersedes - loaded_plugin.superseded_namespaces):
        raise ValueError(f"Supersedes undeclared namespaces: {undeclared}")
    return AgentPluginTools(
        provider=group.provider,
        title=group.title,
        label=group.label,
        tools=tuple(tools[name] for name in sorted(set(group.tools))),
        supersedes=tuple(sorted(supersedes)),
    )


def _checked_namespaces(namespaces: Collection[str]) -> frozenset[str]:
    """Raises `ValueError` for a namespace that is not a registry one."""
    for namespace in namespaces:
        if not namespace or f"{namespace}.".startswith(INTERNAL_TOOL_PREFIX):
            raise ValueError(f"Cannot supersede namespace {namespace!r}")
    return frozenset(namespaces)
