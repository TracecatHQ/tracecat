"""Database reconciliation for workspace sync resources.

The per-resource import logic lives on the resource adapters; this service is a
thin loop that supplies the database context and runs the adapters in dependency
order.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters import (
    NON_WORKFLOW_IMPORT_ADAPTERS,
    ImportedResource,
)
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.schemas import WorkspaceSpec

__all__ = ["ImportedResource", "WorkspaceResourceImportService"]


class WorkspaceResourceImportService(BaseWorkspaceService):
    """Reconcile non-workflow workspace sync resource specs into the DB."""

    service_name = "workspace_resource_import"

    def __init__(
        self,
        session: AsyncSession,
        role: Role | None = None,
        *,
        mapping_provider: VcsProvider = VcsProvider.GITHUB,
    ) -> None:
        """Initialize the importer with the provider namespace for sync mappings."""
        super().__init__(session=session, role=role)
        self._mapping_provider = mapping_provider

    @property
    def _mapping_provider_value(self) -> str:
        """Provider value used for workspace sync resource mappings."""
        return self._mapping_provider.value

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
