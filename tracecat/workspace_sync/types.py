"""Domain types for workspace synchronization."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NamedTuple

from tracecat.exceptions import TracecatValidationError
from tracecat.workspace_sync.enums import SyncResourceType

if TYPE_CHECKING:
    from tracecat.agent.catalog.types import ModelKey
    from tracecat.sync import (
        CatalogMappingRequirement,
        McpIntegrationMappingRequirement,
        PullDiagnostic,
        PullResult,
        SecretStoreMappingRequirement,
    )
    from tracecat.workspace_sync.adapters.base import ResourceAdapter
    from tracecat.workspace_sync.schemas import (
        AgentPresetResourceSpec,
        McpIntegrationHint,
        SecretMetadataResourceSpec,
        WorkflowResourceSpec,
        WorkspaceProjection,
        WorkspaceRemoteSnapshot,
        WorkspaceSpec,
    )
    from tracecat.workspace_sync.transport import VcsTreeSnapshot


type ResourcePathMap = dict[SyncResourceType, dict[str, str]]
"""Repository path of each resource, by type then source id."""

type PathsByType = Mapping[SyncResourceType, Mapping[str, str]]
"""Read-only view of a :data:`ResourcePathMap`, for function parameters."""


type ResourceLocations = dict[SyncResourceType, dict[str, list[str]]]
"""Every primary file path found for each resource, by type then source id.

Usually one path per resource. A broken repository can hold the same ``id``
in several files, for example after two unmerged renames.
"""


@dataclass(frozen=True, slots=True)
class PathOwner:
    """The resource a repository path belongs to."""

    adapter: ResourceAdapter
    source_id: str


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


type WorkflowExportStage = Literal["definition", "dependency"]
type WorkflowExportCode = Literal[
    "publish_required",
    "missing_definition",
    "no_actions",
    "invalid_definition",
    "missing_child_workflow",
]


@dataclass(frozen=True, slots=True)
class PreparedPullPreview:
    """A correlated immutable pull plan paired with its validated review."""

    snapshot: WorkspaceRemoteSnapshot
    preview: PullResult


@dataclass(frozen=True, slots=True)
class PushProjection:
    """What one push writes, and the remote roots it may delete stale files in."""

    projection: WorkspaceProjection
    """What the push writes: local state, plus base bundle entries it keeps."""
    local_projection: WorkspaceProjection
    """Local state alone. It never depends on the branch, so durable pushes
    fingerprint it to detect workspace changes between preview and apply."""
    delete_roots: tuple[str, ...]
    package_include: str | None = None
    """Include file text when the push is a managed resources package export."""


@dataclass(frozen=True, slots=True)
class ParsedWorkspaceFiles:
    """Resources parsed from repository files, and where each one was found."""

    spec: WorkspaceSpec
    diagnostics: list[PullDiagnostic]
    paths: ResourcePathMap
    """Primary file path each resource was read from, by type then source id."""


@dataclass(frozen=True, slots=True)
class PushBase:
    """The branch tree a push writes onto."""

    ref: str
    """Branch name the push starts from: its target, or that branch's base."""
    tree: VcsTreeSnapshot
    """Resource files at that branch head."""


@dataclass(frozen=True, slots=True)
class SyncMappingTarget:
    """Desired sync mapping state for one projected or imported resource."""

    resource_type: str
    source_id: str
    source_path: str
    local_id: uuid.UUID


class SyncCommitConflictError(TracecatValidationError):
    """The target Git ref moved after the reviewed revision was checked."""


class SyncWorkflowImportError(Exception):
    """Retain the failing source workflow after a transaction rollback."""

    def __init__(self, diagnostic: PullDiagnostic, message: str) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic
