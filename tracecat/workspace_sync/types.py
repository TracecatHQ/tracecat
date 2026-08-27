"""Domain types for workspace synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from tracecat.agent.catalog.types import ModelKey
    from tracecat.sync import (
        CatalogMappingRequirement,
        McpIntegrationMappingRequirement,
        PullDiagnostic,
    )
    from tracecat.workspace_sync.schemas import (
        AgentPresetResourceSpec,
        McpIntegrationHint,
        SkillResourceSpec,
        WorkflowResourceSpec,
        WorkspaceRemoteSnapshot,
    )


class CorrelatedAgentPresets(NamedTuple):
    """Catalog-correlated sync specs plus any blocking diagnostics."""

    presets: dict[str, AgentPresetResourceSpec]
    workflows: dict[str, WorkflowResourceSpec]
    diagnostics: list[PullDiagnostic]
    requirements: list[CatalogMappingRequirement]


class AgentPresetCatalogReference(NamedTuple):
    """One preset version referencing a deployment-local source catalog UUID."""

    path: str
    preset_slug: str
    preset_name: str
    version_number: int
    model_key: ModelKey


class WorkflowCatalogReference(NamedTuple):
    """One workflow action referencing a deployment-local source catalog UUID."""

    path: str
    workflow_source_id: str
    workflow_title: str
    action_ref: str
    model_key: ModelKey


type CatalogReference = AgentPresetCatalogReference | WorkflowCatalogReference


@dataclass(frozen=True, slots=True, kw_only=True)
class McpIntegrationCorrelationKey:
    """Portable field subset used to correlate MCP integrations."""

    slug: str
    server_type: str
    auth_type: str


@dataclass(frozen=True, slots=True)
class AgentPresetMcpIntegrationReference:
    """One preset version referencing a workspace-local source MCP integration."""

    path: str
    preset_slug: str
    preset_name: str
    version_number: int
    meta: McpIntegrationHint | None


@dataclass(frozen=True, slots=True)
class WorkflowMcpIntegrationReference:
    """One workflow action referencing a workspace-local source MCP integration."""

    path: str
    workflow_source_id: str
    workflow_title: str
    action_ref: str


@dataclass(frozen=True, slots=True)
class SkillMcpIntegrationReference:
    """One skill head declaring tools from a source MCP integration."""

    path: str
    skill_source_id: str
    skill_name: str
    tool_ids: tuple[str, ...]


type McpIntegrationReference = (
    AgentPresetMcpIntegrationReference
    | WorkflowMcpIntegrationReference
    | SkillMcpIntegrationReference
)


@dataclass(frozen=True, slots=True)
class CorrelatedMcpIntegrationRefs:
    """MCP-correlated sync specs plus any blocking diagnostics."""

    presets: dict[str, AgentPresetResourceSpec]
    workflows: dict[str, WorkflowResourceSpec]
    skills: dict[str, SkillResourceSpec]
    diagnostics: list[PullDiagnostic]
    requirements: list[McpIntegrationMappingRequirement]


class PreparedSnapshot(NamedTuple):
    """Snapshot with deployment-local references resolved, plus any diagnostics."""

    snapshot: WorkspaceRemoteSnapshot
    diagnostics: list[PullDiagnostic]
    catalog_mapping_requirements: list[CatalogMappingRequirement]
    mcp_integration_mapping_requirements: list[McpIntegrationMappingRequirement]
