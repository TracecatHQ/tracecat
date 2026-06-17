"""Workspace sync resource adapters.

Each resource type is described by a single :class:`ResourceAdapter` subclass
that owns its repository paths, parsing/serialization, database projection, and
import logic. The projector, importer, and parser are thin loops over the
adapter registry.
"""

from __future__ import annotations

from tracecat.workspace_sync.adapters.base import (
    CompoundYamlAdapter,
    EnvironmentYamlAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceAdapter,
    ResourceProjection,
    SingleYamlAdapter,
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
    "CompoundYamlAdapter",
    "EnvironmentYamlAdapter",
    "ImportedResource",
    "ProjectedResource",
    "ResourceAdapter",
    "ResourceProjection",
    "SingleYamlAdapter",
    "workspace_spec_from_maps",
]
