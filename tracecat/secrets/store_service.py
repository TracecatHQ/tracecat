"""Organization-owned external secret stores (AWS Secrets Manager)."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from tracecat.audit.logger import audit_log
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    OrganizationSecretStore,
    Secret,
    Workspace,
    WorkspaceSecretStoreAuthorization,
)
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.identifiers import WorkspaceID
from tracecat.secrets.schemas import SecretStoreCreate, SecretStoreUpdate
from tracecat.service import BaseOrgService

_EXTERNAL_ID_BYTES = 24


def generate_store_external_id() -> str:
    """Generate an opaque, server-owned AssumeRole external ID."""
    return f"tracecat-{secrets.token_urlsafe(_EXTERNAL_ID_BYTES)}"


class SecretStoresService(BaseOrgService):
    """Manage organization secret stores and workspace authorizations."""

    service_name = "secret_stores"

    async def list_stores(self) -> Sequence[OrganizationSecretStore]:
        """List stores owned by the current organization."""
        stmt = (
            select(OrganizationSecretStore)
            .where(OrganizationSecretStore.organization_id == self.organization_id)
            .options(selectinload(OrganizationSecretStore.authorizations))
            .order_by(OrganizationSecretStore.name)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_store(self, store_id: uuid.UUID) -> OrganizationSecretStore:
        """Get a store by ID within the current organization."""
        stmt = (
            select(OrganizationSecretStore)
            .where(
                OrganizationSecretStore.organization_id == self.organization_id,
                OrganizationSecretStore.id == store_id,
            )
            .options(selectinload(OrganizationSecretStore.authorizations))
        )
        result = await self.session.execute(stmt)
        store = result.scalar_one_or_none()
        if store is None:
            raise TracecatNotFoundError("Secret store not found")
        return store

    async def count_references(
        self, store_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        """Count workspace secrets referencing each store (metadata only)."""
        if not store_ids:
            return {}
        stmt = (
            select(Secret.store_id, func.count(Secret.id))
            .where(Secret.store_id.in_(store_ids))
            .group_by(Secret.store_id)
        )
        result = await self.session.execute(stmt)
        counts: dict[uuid.UUID, int] = {}
        for store_id, count in result.tuples().all():
            if store_id is not None:
                counts[store_id] = count
        return counts

    @require_scope("org:secret:create")
    @audit_log(resource_type="organization_secret_store", action="create")
    async def create_store(self, params: SecretStoreCreate) -> OrganizationSecretStore:
        """Create a store. The external ID is generated and persisted here."""
        store = OrganizationSecretStore(
            organization_id=self.organization_id,
            name=params.name,
            description=params.description,
            provider=params.provider,
            role_arn=params.role_arn,
            region=params.region,
            external_id=generate_store_external_id(),
            enabled=params.enabled,
        )
        self.session.add(store)
        await self.session.commit()
        await self.session.refresh(store, attribute_names=["authorizations"])
        return store

    @require_scope("org:secret:update")
    @audit_log(resource_type="organization_secret_store", action="update")
    async def update_store(
        self, store: OrganizationSecretStore, params: SecretStoreUpdate
    ) -> None:
        """Update store metadata. The external ID is never changed."""
        for field, value in params.model_dump(exclude_unset=True).items():
            setattr(store, field, value)
        self.session.add(store)
        await self.session.commit()

    @require_scope("org:secret:delete")
    @audit_log(resource_type="organization_secret_store", action="delete")
    async def delete_store(self, store: OrganizationSecretStore) -> None:
        """Delete a store. Rejected while workspace secrets still reference it."""
        counts = await self.count_references([store.id])
        if reference_count := counts.get(store.id, 0):
            raise TracecatConflictError(
                f"Secret store is referenced by {reference_count} workspace"
                " secret(s). Remove those references first.",
                detail={"reference_count": reference_count},
            )
        await self.session.delete(store)
        await self.session.commit()

    @require_scope("org:secret:update")
    @audit_log(resource_type="organization_secret_store", action="update")
    async def authorize_workspace(
        self, store: OrganizationSecretStore, workspace_id: WorkspaceID
    ) -> WorkspaceSecretStoreAuthorization:
        """Allow a workspace in this organization to reference the store."""
        workspace_stmt = select(Workspace.id).where(
            Workspace.id == workspace_id,
            Workspace.organization_id == self.organization_id,
        )
        if (await self.session.execute(workspace_stmt)).scalar_one_or_none() is None:
            raise TracecatNotFoundError("Workspace not found in this organization")

        existing_stmt = select(WorkspaceSecretStoreAuthorization).where(
            WorkspaceSecretStoreAuthorization.store_id == store.id,
            WorkspaceSecretStoreAuthorization.workspace_id == workspace_id,
        )
        if existing := (await self.session.execute(existing_stmt)).scalar_one_or_none():
            return existing

        authorization = WorkspaceSecretStoreAuthorization(
            organization_id=self.organization_id,
            workspace_id=workspace_id,
            store_id=store.id,
        )
        self.session.add(authorization)
        await self.session.commit()
        return authorization

    @require_scope("org:secret:update")
    @audit_log(resource_type="organization_secret_store", action="revoke")
    async def revoke_workspace(
        self, store: OrganizationSecretStore, workspace_id: WorkspaceID
    ) -> None:
        """Revoke a workspace authorization.

        Rejected while that workspace still holds secrets referencing the store
        so runtime never silently loses an authorized binding.
        """
        reference_stmt = select(func.count(Secret.id)).where(
            Secret.store_id == store.id, Secret.workspace_id == workspace_id
        )
        reference_count = (await self.session.execute(reference_stmt)).scalar_one()
        if reference_count:
            raise TracecatConflictError(
                f"Workspace still has {reference_count} secret(s) referencing this"
                " store. Remove those references first.",
                detail={"reference_count": reference_count},
            )
        stmt = select(WorkspaceSecretStoreAuthorization).where(
            WorkspaceSecretStoreAuthorization.store_id == store.id,
            WorkspaceSecretStoreAuthorization.workspace_id == workspace_id,
        )
        authorization = (await self.session.execute(stmt)).scalar_one_or_none()
        if authorization is None:
            raise TracecatNotFoundError("Workspace authorization not found")
        await self.session.delete(authorization)
        await self.session.commit()
