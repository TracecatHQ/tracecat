"""Database projection for workspace sync resources.

The per-resource projection logic lives on the resource adapters; this service
is a thin loop that supplies the database context and assembles the combined
projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tracecat.workspace_sync.adapters import (
    NON_WORKFLOW_RESOURCE_ADAPTERS,
    ProjectedResource,
    SyncMappingService,
    workspace_spec_from_maps,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import WorkspaceSpec

__all__ = [
    "ProjectedResource",
    "WorkspaceResourceProjection",
    "WorkspaceResourceProjector",
]


@dataclass(frozen=True, slots=True)
class WorkspaceResourceProjection:
    """Combined projection of all non-workflow workspace resources."""

    spec: WorkspaceSpec
    """Assembled workspace spec holding each resource type's Git-owned specs."""
    resources: list[ProjectedResource]
    """Flattened identities linking each ``source_id`` to its ``local_id``."""


class WorkspaceResourceProjector(SyncMappingService):
    """Project non-workflow workspace config resources into sync specs."""

    service_name = "workspace_resource_projector"

    async def project_non_workflow_resources(
        self,
        *,
        resource_types: set[SyncResourceType] | None = None,
    ) -> WorkspaceResourceProjection:
        """Project every non-workflow resource type into one combined spec.

        Runs each selected adapter in :data:`NON_WORKFLOW_RESOURCE_ADAPTERS`,
        keys its specs by ``spec_attr``, and assembles them into a
        :class:`WorkspaceResourceProjection` alongside the projected identities.
        A ``None`` resource type filter projects every non-workflow type.
        """
        specs_by_attr: dict[str, dict[str, Any]] = {}
        resources: list[ProjectedResource] = []
        for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS:
            if (
                resource_types is not None
                and adapter.resource_type not in resource_types
            ):
                continue
            projection = await adapter.project(self)
            specs_by_attr[adapter.spec_attr] = projection.specs
            resources.extend(projection.resources)
        return WorkspaceResourceProjection(
            spec=workspace_spec_from_maps(specs_by_attr),
            resources=resources,
        )
