"""Domain types for workspace synchronization."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from tracecat.exceptions import TracecatValidationError

if TYPE_CHECKING:
    from tracecat.agent.catalog.types import ModelKey
    from tracecat.sync import (
        CatalogMappingRequirement,
        McpIntegrationMappingRequirement,
        PullDiagnostic,
        PullResult,
        SecretStoreMappingRequirement,
    )
    from tracecat.workspace_sync.schemas import (
        AgentPresetResourceSpec,
        McpIntegrationHint,
        SecretMetadataResourceSpec,
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
    """One preset head referencing a deployment-local source catalog UUID."""

    path: str
    preset_slug: str
    preset_name: str
    version_number: int | None
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
    """One preset head referencing a workspace-local source MCP integration."""

    path: str
    preset_slug: str
    preset_name: str
    version_number: int | None
    meta: McpIntegrationHint | None


@dataclass(frozen=True, slots=True)
class WorkflowMcpIntegrationReference:
    """One workflow action referencing a workspace-local source MCP integration."""

    path: str
    workflow_source_id: str
    workflow_title: str
    action_ref: str


type McpIntegrationReference = (
    AgentPresetMcpIntegrationReference | WorkflowMcpIntegrationReference
)


@dataclass(frozen=True, slots=True)
class CorrelatedMcpIntegrationRefs:
    """MCP-correlated sync specs plus any blocking diagnostics."""

    presets: dict[str, AgentPresetResourceSpec]
    workflows: dict[str, WorkflowResourceSpec]
    diagnostics: list[PullDiagnostic]
    requirements: list[McpIntegrationMappingRequirement]


@dataclass(frozen=True, slots=True)
class CorrelatedSecretStores:
    """Secret metadata with mapped store names, plus diagnostics and requirements."""

    secret_metadata: dict[str, SecretMetadataResourceSpec]
    diagnostics: list[PullDiagnostic]
    requirements: list[SecretStoreMappingRequirement]


@dataclass(frozen=True, slots=True)
class PreparedSnapshot:
    """Snapshot with deployment-local references resolved, plus any diagnostics."""

    snapshot: WorkspaceRemoteSnapshot
    diagnostics: list[PullDiagnostic]
    catalog_mapping_requirements: list[CatalogMappingRequirement]
    mcp_integration_mapping_requirements: list[McpIntegrationMappingRequirement]
    secret_store_mapping_requirements: list[SecretStoreMappingRequirement]
    library_skill_installs: list[str]


@dataclass(frozen=True, slots=True)
class PreparedPullPreview:
    """A correlated immutable pull plan paired with its validated review."""

    snapshot: WorkspaceRemoteSnapshot
    preview: PullResult


@dataclass(frozen=True, slots=True)
class SyncMappingTarget:
    """Desired sync mapping state for one projected or imported resource."""

    resource_type: str
    source_id: str
    source_path: str
    local_id: uuid.UUID


class SyncCommitConflictError(TracecatValidationError):
    """The target Git ref moved after the reviewed revision was checked."""
