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

from typing import cast

from pydantic import BaseModel

from tracecat.workspace_sync.adapters.base import (
    DirectoryManifestAdapter,
    path_segment,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    WORKFLOW_DEFINITION_FILENAME,
    WORKFLOW_ROOT,
    WorkflowResourceSpec,
)


class WorkflowAdapter(DirectoryManifestAdapter):
    """Registry adapter for workflow paths; projection/import live in the service."""

    resource_type = SyncResourceType.WORKFLOW
    spec_attr = "workflows"
    model = WorkflowResourceSpec
    read_scope = "workflow:read"
    create_scope = "workflow:create"
    update_scope = "workflow:update"
    root = WORKFLOW_ROOT
    filename = WORKFLOW_DEFINITION_FILENAME

    def path_key(self, source_id: str, spec: BaseModel) -> str:
        """Name the workflow folder after its title, as shown in the app."""
        title = cast(WorkflowResourceSpec, spec).definition.title
        return path_segment(title, fallback=path_segment(source_id))

    def display_name(self, spec: BaseModel) -> str | None:
        """Use the workflow definition title, falling back to the base label."""
        if isinstance(spec, WorkflowResourceSpec):
            return spec.definition.title
        return super().display_name(spec)
