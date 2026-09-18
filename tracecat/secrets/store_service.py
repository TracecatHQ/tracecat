"""Organization-owned external secret stores (AWS Secrets Manager)."""

from __future__ import annotations

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
from tracecat.db.rls import set_rls_context, set_rls_context_from_role
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.identifiers import WorkspaceID
from tracecat.secrets.backends import get_backend, parse_store_config
from tracecat.secrets.schemas import SecretStoreCreate, SecretStoreUpdate
from tracecat.service import BaseOrgService


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
        self,
        store_ids: Sequence[uuid.UUID],
        *,
        workspace_id: WorkspaceID | None = None,
    ) -> dict[uuid.UUID, int]:
        """Count workspace secrets referencing each store (metadata only)."""
        if not store_ids:
            return {}
        stmt = (
            select(OrganizationSecretStore.id, func.count(Secret.id))
            .select_from(Secret)
            .join(
                OrganizationSecretStore, Secret.store_id == OrganizationSecretStore.id
            )
            .where(
                Secret.store_id.in_(store_ids),
                OrganizationSecretStore.organization_id == self.organization_id,
            )
            .group_by(OrganizationSecretStore.id)
        )
        if workspace_id is not None:
            stmt = stmt.where(Secret.workspace_id == workspace_id)
        # Org sessions carry no workspace context, so `secret` rows are hidden
        # by RLS. Bypass on this same transaction; restore before returning.
        await set_rls_context(
            self.session, self.organization_id, None, self.role.user_id, bypass=True
        )
        try:
            result = await self.session.execute(stmt)
        finally:
            await set_rls_context_from_role(self.session, self.role)
        return dict(result.tuples().all())

    @require_scope("org:secret:create")
    @audit_log(resource_type="organization_secret_store", action="create")
    async def create_store(self, params: SecretStoreCreate) -> OrganizationSecretStore:
        """Create a store. Server-owned config fields are generated here."""
        config = get_backend(params.provider).new_config(params.config)
        store = OrganizationSecretStore(
            organization_id=self.organization_id,
            name=params.name,
            description=params.description,
            provider=params.provider,
            config=config.model_dump(mode="json"),
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
        """Update store metadata. Server-owned config fields are never changed."""
        fields = params.model_dump(exclude_unset=True)
        fields.pop("config", None)
        if params.config is not None:
            config = get_backend(store.provider).update_config(
                parse_store_config(store), params.config
            )
            store.config = config.model_dump(mode="json")
        for field, value in fields.items():
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
        counts = await self.count_references([store.id], workspace_id=workspace_id)
        if reference_count := counts.get(store.id, 0):
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
