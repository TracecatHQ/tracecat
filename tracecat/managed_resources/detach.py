"""Detach: turn a managed resource into an editable, customer-owned copy.

The managed original stays locked and keeps receiving rollouts. The copy is
an ordinary resource with a new name, built from the original's exported
spec, so detach works the same way for every resource type:

1. Project the original exactly as workspace sync would export it.
2. Give the spec the new name (workflow alias, preset or skill slug, or name).
3. Import it into the same workspace under a throwaway mapping namespace, so
   the importer creates a new row instead of finding the original.

References inside the copy are written by name, so they still resolve to
the managed originals. For example, a copied workflow still calls the
managed child workflow. Triggers on a copied workflow start offline, so the
copy never runs next to the original by accident.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy import delete, select

from tracecat.audit.logger import audit_log
from tracecat.db.models import (
    ManagedDetachedCopy,
    ManagedResource,
    WorkspaceSyncResourceMapping,
)
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.managed_resources.identities import spec_identity, workspace_identities
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters import RESOURCE_ADAPTERS_BY_TYPE
from tracecat.workspace_sync.adapters.base import path_segment
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.schemas import (
    WorkflowResourceSpec,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
)
from tracecat.workspace_sync.service import WorkspaceSyncService

_SLUG_TYPES = frozenset({SyncResourceType.AGENT_PRESET, SyncResourceType.SKILL})


class DetachedCopy(BaseModel):
    """The copy a detach created."""

    resource_type: SyncResourceType
    local_id: uuid.UUID
    name: str
    original_local_id: uuid.UUID


class ManagedDetachService(BaseWorkspaceService):
    """Create editable copies of managed resources for workspace admins."""

    service_name = "managed_detach"

    @audit_log(resource_type="managed_resource", action="detach")
    async def detach(
        self,
        *,
        resource_type: SyncResourceType,
        local_id: uuid.UUID,
        new_name: str,
    ) -> DetachedCopy:
        """Copy a managed resource under ``new_name``.

        Raises:
            TracecatNotFoundError: The resource does not exist.
            TracecatValidationError: The resource is not managed, or the
                name is empty.
            TracecatConflictError: Another resource already uses the name.
        """
        new_name = new_name.strip()
        if not new_name:
            raise TracecatValidationError("A name for the copy is required")
        managed = await self.session.scalar(
            select(ManagedResource).where(
                ManagedResource.workspace_id == self.workspace_id,
                ManagedResource.resource_type == resource_type.value,
                ManagedResource.local_id == local_id,
            )
        )
        if managed is None:
            raise TracecatValidationError("Only managed resources can be detached")

        namespace = f"detach-{uuid.uuid4().hex[:12]}"
        sync = WorkspaceSyncService(
            self.session,
            self.role,
            provider=VcsProvider.GITHUB,
            mapping_namespace=namespace,
        )
        adapter = RESOURCE_ADAPTERS_BY_TYPE[resource_type]
        projection = await sync.project_workspace(
            resource_ids={resource_type: {local_id}}, create_missing_mappings=False
        )
        source_id = next(
            (
                target.source_id
                for target in projection.mapping_targets
                if target.resource_type == resource_type.value
                and target.local_id == local_id
            ),
            None,
        )
        if source_id is None:
            raise TracecatNotFoundError("Managed resource not found")
        original = adapter.specs(projection.spec)[source_id]
        original_name = adapter.display_name(original) or source_id
        await self._ensure_name_free(resource_type, original, new_name)

        copy_source_id = path_segment(new_name)
        copy = _renamed(resource_type, original, new_name, copy_source_id)
        snapshot = WorkspaceRemoteSnapshot(
            commit_sha="",
            files={},
            spec=WorkspaceSpec.model_validate(
                {adapter.spec_attr: {copy_source_id: copy}}
            ),
        )
        prepared = await sync.prepare_pull_preview(snapshot, sync_schedules=False)
        if not prepared.preview.success:
            raise TracecatValidationError(
                "Could not copy the resource: "
                + "; ".join(d.message for d in prepared.preview.diagnostics)
            )
        await sync.import_prepared_snapshot(prepared.snapshot, sync_schedules=False)
        copy_local_id = (
            await adapter.local_ids_by_source_id(sync, [copy_source_id])
        ).get(copy_source_id)
        if copy_local_id is None:
            raise TracecatValidationError("The copy was not created")
        # The throwaway namespace only existed to stop the importer from
        # finding the original; the copy is an ordinary resource from now on.
        await self.session.execute(
            delete(WorkspaceSyncResourceMapping).where(
                WorkspaceSyncResourceMapping.workspace_id == self.workspace_id,
                WorkspaceSyncResourceMapping.provider == namespace,
            )
        )
        self.session.add(
            ManagedDetachedCopy(
                workspace_id=self.workspace_id,
                resource_type=resource_type.value,
                local_id=copy_local_id,
                original_local_id=local_id,
                original_source_id=managed.source_id,
                original_name=original_name,
                detached_by=self.role.user_id,
            )
        )
        await self.session.commit()
        return DetachedCopy(
            resource_type=resource_type,
            local_id=copy_local_id,
            name=new_name,
            original_local_id=local_id,
        )

    async def _ensure_name_free(
        self,
        resource_type: SyncResourceType,
        original: BaseModel,
        new_name: str,
    ) -> None:
        """Refuse a name already used by a resource of the same type.

        The importer adopts an existing resource with the same name, so a
        taken name would overwrite that resource instead of copying.
        """
        wanted = spec_identity(
            resource_type, _renamed(resource_type, original, new_name, "probe")
        )
        identities = await workspace_identities(
            self.session, self.workspace_id, resource_types=[resource_type]
        )
        if wanted is not None and (resource_type.value, wanted) in identities:
            raise TracecatConflictError(
                f"{new_name!r} is already used by another {resource_type.value}"
            )


def _renamed(
    resource_type: SyncResourceType,
    spec: BaseModel,
    new_name: str,
    source_id: str,
) -> BaseModel:
    """Return a copy of ``spec`` with a new identity and offline triggers."""
    if isinstance(spec, WorkflowResourceSpec):
        return spec.model_copy(
            update={
                "id": source_id,
                "alias": new_name,
                "schedules": None,
                "definition": spec.definition.model_copy(
                    update={"title": f"{spec.definition.title} ({new_name})"[:100]}
                ),
                "webhook": spec.webhook.model_copy(update={"status": "offline"})
                if spec.webhook
                else None,
                "case_trigger": spec.case_trigger.model_copy(
                    update={"status": "offline"}
                )
                if spec.case_trigger
                else None,
                "form": None,
            }
        )
    update: dict[str, object] = {"id": source_id}
    if resource_type in _SLUG_TYPES:
        update |= {"slug": new_name, "name": new_name}
    else:
        update["name"] = new_name
    return spec.model_copy(update=update)
