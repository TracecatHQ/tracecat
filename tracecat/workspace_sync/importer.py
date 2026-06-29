"""Database reconciliation for workspace sync resources.

The per-resource import logic lives on the resource adapters; this service is a
thin loop that supplies the database context and runs the adapters in dependency
order.
"""

from __future__ import annotations

from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters import (
    NON_WORKFLOW_IMPORT_ADAPTERS,
    ImportedResource,
)
from tracecat.workspace_sync.schemas import WorkspaceSpec

__all__ = ["ImportedResource", "WorkspaceResourceImportService"]


class WorkspaceResourceImportService(BaseWorkspaceService):
    """Reconcile non-workflow workspace sync resource specs into the DB."""

    service_name = "workspace_resource_import"

    async def import_non_workflow_resources(
        self,
        spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile every non-workflow resource in ``spec`` into the database.

        Runs the :data:`NON_WORKFLOW_IMPORT_ADAPTERS` in dependency order and
        returns the flattened :class:`ImportedResource` identities.
        """
        imported: list[ImportedResource] = []
        for adapter in NON_WORKFLOW_IMPORT_ADAPTERS:
            imported.extend(await adapter.import_specs(self, spec))
        return imported
