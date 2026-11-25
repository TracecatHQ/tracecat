"""Contract types between Tracecat and the plugins that register internal tools."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.mcp.internal_tools import InternalToolHandler
from tracecat.auth.types import Role


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


@dataclass(frozen=True, slots=True)
class AgentPluginToolGroups:
    """An agent's plugin tool groups, and the plugins that could not describe theirs."""

    groups: tuple[AgentPluginTools, ...] = ()
    failed_plugins: tuple[str, ...] = ()
    """Entry point names of plugins that failed to load or answer."""


class InternalToolPlugin(Protocol):
    """Registers internal tools and grants them per session.

    `tools` and `superseded_namespaces` are read once per process. `session_tools`
    runs once per scope compile and `agent_tool_groups` per builder read; both run
    under the caller's role and must not block the event loop. A session's
    superseded namespaces must match the `supersedes` of its agent's groups.
    """

    def tools(self) -> Sequence[PluginInternalTool]:
        """Every tool this plugin serves."""
        ...

    def superseded_namespaces(self) -> Sequence[str]:
        """Every registry namespace a session's answer may supersede.

        A session whose answer fails supersedes all of them.
        """
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
