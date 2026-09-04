"""Domain types for workspace skills."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResolvedSkillRef:
    """Exact published skill version resolved for agent execution."""

    skill_id: uuid.UUID
    skill_name: str
    skill_version_id: uuid.UUID
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class ResolvedSkillMcpTool:
    """Publish-time MCP reference resolved to a non-resurrectable UUID."""

    tool_id: str
    mcp_integration_id: uuid.UUID
    tool_name: str | None


@dataclass(frozen=True, slots=True)
class SkillToolProjection:
    """Validated projection materialized for one immutable skill version."""

    registry_tool_ids: tuple[str, ...] = ()
    mcp_tools: tuple[ResolvedSkillMcpTool, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillMcpGrant:
    """Effective grant for one MCP integration.

    ``tool_names=None`` grants the whole integration. A non-empty frozenset is
    an explicit tool subset.
    """

    mcp_integration_id: uuid.UUID
    tool_names: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class SkillToolGrants:
    """Actor-authorized effective tool grants compiled from attached skills."""

    registry_tool_ids: tuple[str, ...] = ()
    mcp_grants: tuple[SkillMcpGrant, ...] = ()
