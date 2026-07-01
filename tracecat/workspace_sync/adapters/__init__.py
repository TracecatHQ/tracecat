"""Workspace sync resource adapters.

Most resource types are fully described by one :class:`ResourceAdapter`
subclass that owns repository paths, parsing/serialization, database projection,
and import logic. Workflows are the exception: their adapter is only a registry
shim for path/spec metadata, while ``WorkspaceSyncService`` handles workflow
projection/import because it needs DSL resolution and workflow-store services.
"""

from __future__ import annotations

from tracecat.workspace_sync.adapters.base import (
    DirectoryManifestAdapter,
    EnvironmentScopedManifestAdapter,
    FlatManifestAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceAdapter,
    ResourceProjection,
    SyncMappingService,
)
from tracecat.workspace_sync.adapters.registry import (
    AGENT_PRESET_RESOURCE_ADAPTER,
    CASE_DROPDOWN_RESOURCE_ADAPTER,
    CASE_DURATION_RESOURCE_ADAPTER,
    CASE_FIELD_RESOURCE_ADAPTER,
    CASE_TAG_RESOURCE_ADAPTER,
    NON_WORKFLOW_IMPORT_ADAPTERS,
    NON_WORKFLOW_RESOURCE_ADAPTERS,
    RESOURCE_ADAPTERS_BY_TYPE,
    SECRET_METADATA_RESOURCE_ADAPTER,
    SKILL_RESOURCE_ADAPTER,
    TABLE_RESOURCE_ADAPTER,
    VARIABLE_RESOURCE_ADAPTER,
    WORKFLOW_RESOURCE_ADAPTER,
    WORKSPACE_RESOURCE_ADAPTERS,
    workspace_spec_from_maps,
)

__all__ = [
    "AGENT_PRESET_RESOURCE_ADAPTER",
    "CASE_DROPDOWN_RESOURCE_ADAPTER",
    "CASE_DURATION_RESOURCE_ADAPTER",
    "CASE_FIELD_RESOURCE_ADAPTER",
    "CASE_TAG_RESOURCE_ADAPTER",
    "NON_WORKFLOW_IMPORT_ADAPTERS",
    "NON_WORKFLOW_RESOURCE_ADAPTERS",
    "RESOURCE_ADAPTERS_BY_TYPE",
    "SECRET_METADATA_RESOURCE_ADAPTER",
    "SKILL_RESOURCE_ADAPTER",
    "TABLE_RESOURCE_ADAPTER",
    "VARIABLE_RESOURCE_ADAPTER",
    "WORKFLOW_RESOURCE_ADAPTER",
    "WORKSPACE_RESOURCE_ADAPTERS",
    "DirectoryManifestAdapter",
    "EnvironmentScopedManifestAdapter",
    "FlatManifestAdapter",
    "ImportedResource",
    "ProjectedResource",
    "ResourceAdapter",
    "ResourceProjection",
    "SyncMappingService",
    "workspace_spec_from_maps",
]
