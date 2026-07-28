"""Domain types for workspace synchronization."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from tracecat.agent.catalog.types import ModelKey
    from tracecat.sync import CatalogMappingRequirement, PullDiagnostic
    from tracecat.workspace_sync.schemas import (
        AgentPresetResourceSpec,
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


class PreparedSnapshot(NamedTuple):
    """Snapshot with deployment-local references resolved, plus any diagnostics."""

    snapshot: WorkspaceRemoteSnapshot
    diagnostics: list[PullDiagnostic]
    catalog_mapping_requirements: list[CatalogMappingRequirement]
