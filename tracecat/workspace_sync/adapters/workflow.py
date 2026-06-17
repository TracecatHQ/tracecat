"""Workflow resource adapter.

Workflows are projected and imported directly by ``WorkspaceSyncService`` (they
need DSL resolution and closure handling), so this adapter only contributes path
and parsing metadata and inherits the no-op project/import-specs defaults.
"""

from __future__ import annotations

from pydantic import BaseModel

from tracecat.workspace_sync.adapters.base import ResourceAdapter
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    WorkflowResourceSpec,
    WorkspaceManifestResources,
)
from tracecat.workspace_sync.workflow import (
    workflow_source_id_from_path,
    workflow_source_path,
)


class WorkflowAdapter(ResourceAdapter):
    resource_type = SyncResourceType.WORKFLOW
    spec_attr = "workflows"
    model = WorkflowResourceSpec

    def source_path(self, source_id: str) -> str:
        return workflow_source_path(source_id)

    def source_id_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> str | None:
        return workflow_source_id_from_path(
            path,
            workflow_root=roots.workflows.strip("/"),
        )

    def display_name(self, spec: BaseModel) -> str | None:
        if isinstance(spec, WorkflowResourceSpec):
            return spec.definition.title
        return super().display_name(spec)
