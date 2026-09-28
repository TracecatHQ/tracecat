"""Workflow adapter registry shim.

Unlike the non-workflow adapters, this class does not project ORM rows or import
specs by itself. Workflows need DSL resolution, dependency closure handling, and
``WorkflowImportService`` integration, so ``WorkspaceSyncService`` owns that
runtime behavior.

This adapter keeps workflows visible to the generic workspace-sync registry for
path lookup, manifest/spec assembly, dependency diagnostics, and preview labels.
Workflow-specific conversion and YAML parsing live in
``tracecat.workspace_sync.workflow``.
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
    """Registry adapter for workflow paths; projection/import live in the service."""

    resource_type = SyncResourceType.WORKFLOW
    spec_attr = "workflows"
    model = WorkflowResourceSpec
    read_scope = "workflow:read"
    create_scope = "workflow:create"
    update_scope = "workflow:update"

    def source_path(self, source_id: str) -> str:
        """Delegate to :func:`workflow_source_path` for the workflow layout."""
        return workflow_source_path(source_id)

    def source_id_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> str | None:
        """Delegate to :func:`workflow_source_id_from_path` under the workflow root."""
        return workflow_source_id_from_path(
            path,
            workflow_root=roots.workflows.strip("/"),
        )

    def display_name(self, spec: BaseModel) -> str | None:
        """Use the workflow definition title, falling back to the base label."""
        if isinstance(spec, WorkflowResourceSpec):
            return spec.definition.title
        return super().display_name(spec)
