"""Read helpers for managed resource state in a target workspace."""

from __future__ import annotations

import uuid
from collections.abc import Collection

from sqlalchemy import select

from tracecat.db.models import ManagedResource
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.enums import SyncResourceType


class ManagedResourceService(BaseWorkspaceService):
    """Look up which resources in the caller's workspace are managed."""

    service_name = "managed_resources"

    async def get_managed_resource(
        self, resource_type: SyncResourceType, local_id: uuid.UUID
    ) -> ManagedResource | None:
        """Return the managed resource row for a local resource, if any.

        Args:
            resource_type: Workspace sync resource type of the local resource.
            local_id: Workspace-local resource UUID.

        Returns:
            The managed resource row, or None when the resource is unmanaged.
        """
        rows = await self.get_managed_resources(resource_type, [local_id])
        return rows.get(local_id)

    async def get_managed_resources(
        self, resource_type: SyncResourceType, local_ids: Collection[uuid.UUID]
    ) -> dict[uuid.UUID, ManagedResource]:
        """Return managed resource rows keyed by local id.

        Args:
            resource_type: Workspace sync resource type of the local resources.
            local_ids: Workspace-local resource UUIDs to check.

        Returns:
            A mapping that contains only the managed local ids.
        """
        if not local_ids:
            return {}
        stmt = select(ManagedResource).where(
            ManagedResource.workspace_id == self.workspace_id,
            ManagedResource.resource_type == resource_type,
            ManagedResource.local_id.in_(local_ids),
        )
        result = await self.session.execute(stmt)
        return {row.local_id: row for row in result.scalars().all()}
