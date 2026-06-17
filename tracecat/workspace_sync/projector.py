"""Database projection for workspace sync resources.

The per-resource projection logic lives on the resource adapters; this service
is a thin loop that supplies the database context and assembles the combined
projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters import (
    NON_WORKFLOW_RESOURCE_ADAPTERS,
    ProjectedResource,
    workspace_spec_from_maps,
)
from tracecat.workspace_sync.schemas import WorkspaceSpec

__all__ = [
    "ProjectedResource",
    "WorkspaceResourceProjection",
    "WorkspaceResourceProjector",
]


@dataclass(frozen=True, slots=True)
class WorkspaceResourceProjection:
    spec: WorkspaceSpec
    resources: list[ProjectedResource]


class WorkspaceResourceProjector(BaseWorkspaceService):
    """Project non-workflow workspace config resources into sync specs."""

    service_name = "workspace_resource_projector"

    async def project_non_workflow_resources(self) -> WorkspaceResourceProjection:
        specs_by_attr: dict[str, dict[str, Any]] = {}
        resources: list[ProjectedResource] = []
        for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS:
            projection = await adapter.project(self)
            specs_by_attr[adapter.spec_attr] = projection.specs
            resources.extend(projection.resources)
        return WorkspaceResourceProjection(
            spec=workspace_spec_from_maps(specs_by_attr),
            resources=resources,
        )
