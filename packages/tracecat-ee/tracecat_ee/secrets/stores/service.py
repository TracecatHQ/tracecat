"""Organization-owned external secret stores."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tracecat.audit.logger import audit_log
from tracecat.auth.secrets import get_db_encryption_key
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
from tracecat.pagination import Page, PageParams, paginate
from tracecat.secrets.encryption import encrypt_value
from tracecat.secrets.schemas import (
    SecretStoreConfig,
    SecretStoreCreate,
    SecretStoreUpdate,
    SecretStoreWorkspaceUsage,
)
from tracecat.service import BaseOrgService
from tracecat_ee.secrets.stores.backends import (
    AnyBackend,
    get_backend,
    parse_store_config,
    store_is_ready,
)

_NOT_READY_MESSAGE = "Finish setting up the store before enabling it."
_MAX_ACCESS_TOKEN_LENGTH = 4096


def _encrypt_access_token(token: SecretStr) -> bytes:
    return encrypt_value(
        token.get_secret_value().encode("utf-8"), key=get_db_encryption_key()
    )


class SecretStoresService(BaseOrgService):
    """Manage organization secret stores and workspace authorizations."""

    service_name = "secret_stores"

    async def list_stores(self, page: PageParams) -> Page[OrganizationSecretStore]:
        """List stores owned by the current organization."""
        stmt = (
            select(OrganizationSecretStore)
            .where(OrganizationSecretStore.organization_id == self.organization_id)
            .options(selectinload(OrganizationSecretStore.authorizations))
        )
        return await paginate(
            self.session,
            stmt,
            page=page,
            order_by=(
                OrganizationSecretStore.created_at.asc(),
                OrganizationSecretStore.id.asc(),
            ),
        )

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

    async def workspace_usage(
        self, store_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[SecretStoreWorkspaceUsage]]:
        """Count referencing secrets per store and workspace, most used first."""
        if not store_ids:
            return {}
        count = func.count(Secret.id)
        stmt = (
            select(Secret.store_id, Secret.workspace_id, count)
            .join(
                OrganizationSecretStore, Secret.store_id == OrganizationSecretStore.id
            )
            .where(
                Secret.store_id.in_(store_ids),
                OrganizationSecretStore.organization_id == self.organization_id,
            )
            .group_by(Secret.store_id, Secret.workspace_id)
            .order_by(count.desc(), Secret.workspace_id)
        )
        # Same RLS bypass as count_references: org sessions cannot see `secret`.
        await set_rls_context(
            self.session, self.organization_id, None, self.role.user_id, bypass=True
        )
        try:
            result = await self.session.execute(stmt)
        finally:
            await set_rls_context_from_role(self.session, self.role)
        usage: dict[uuid.UUID, list[SecretStoreWorkspaceUsage]] = {}
        for store_id, workspace_id, secret_count in result.tuples():
            if store_id is None:
                continue
            usage.setdefault(store_id, []).append(
                SecretStoreWorkspaceUsage(
                    workspace_id=workspace_id, secret_count=secret_count
                )
            )
        return usage

    async def _saved_references(self, store_id: uuid.UUID) -> set[str]:
        """Distinct remote references saved against a store (metadata only)."""
        stmt = (
            select(Secret.remote_reference)
            .join(
                OrganizationSecretStore, Secret.store_id == OrganizationSecretStore.id
            )
            .where(
                Secret.store_id == store_id,
                OrganizationSecretStore.organization_id == self.organization_id,
                Secret.remote_reference.is_not(None),
            )
            .distinct()
        )
        # Same RLS bypass as count_references: org sessions cannot see `secret`.
        await set_rls_context(
            self.session, self.organization_id, None, self.role.user_id, bypass=True
        )
        try:
            result = await self.session.scalars(stmt)
            return {reference for reference in result if reference is not None}
        finally:
            await set_rls_context_from_role(self.session, self.role)

    @staticmethod
    def _check_store_settings(
        backend: AnyBackend,
        config: SecretStoreConfig,
        *,
        token: SecretStr | None,
        token_set: bool,
        enabled: bool,
    ) -> None:
        """Check a new access token fits the store, and the store can be enabled.

        The length is checked here rather than in the schema, whose 422 would
        echo the token.
        """
        if token is not None:
            if not 1 <= len(token.get_secret_value()) <= _MAX_ACCESS_TOKEN_LENGTH:
                raise ValueError(
                    f"The access token must be 1 to {_MAX_ACCESS_TOKEN_LENGTH}"
                    " characters."
                )
            if not backend.uses_access_token:
                raise ValueError("This store's provider does not use an access token.")
        if enabled and not store_is_ready(backend, config, has_access_token=token_set):
            if backend.is_ready(config):
                raise ValueError("Set an access token before enabling this store.")
            raise ValueError(_NOT_READY_MESSAGE)

    @require_scope("org:secret:create")
    @audit_log(resource_type="organization_secret_store", action="create")
    async def create_store(self, params: SecretStoreCreate) -> OrganizationSecretStore:
        """Create a store. Server-owned config fields are generated here."""
        backend = get_backend(params.provider)
        config = backend.new_config(params.config)
        self._check_store_settings(
            backend,
            config,
            token=params.access_token,
            token_set=params.access_token is not None,
            enabled=params.enabled,
        )
        await backend.check_config(config)
        store = OrganizationSecretStore(
            organization_id=self.organization_id,
            name=params.name,
            description=params.description,
            provider=params.provider,
            config=config.model_dump(mode="json"),
            enabled=params.enabled,
            all_workspaces=params.all_workspaces,
            encrypted_access_token=(
                _encrypt_access_token(params.access_token)
                if params.access_token is not None
                else None
            ),
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
        # Written to its encrypted column below, never set as given.
        fields.pop("access_token", None)
        if params.config is not None and params.config.provider != store.provider:
            raise ValueError("config.provider must match the store's provider")
        backend = get_backend(store.provider)
        enabled = fields.get("enabled", store.enabled)
        saved: SecretStoreConfig | None = None
        config: SecretStoreConfig | None = None
        # Parse the saved config only when needed, so a store with an
        # unparseable config can still be renamed or disabled.
        if enabled or params.config is not None or params.access_token is not None:
            saved = current = parse_store_config(store)
            if params.config is not None:
                current = backend.update_config(saved, params.config)
            config = current
            self._check_store_settings(
                backend,
                current,
                token=params.access_token,
                token_set=(
                    params.access_token is not None
                    or store.encrypted_access_token is not None
                ),
                enabled=enabled,
            )
        if config is not None and config != saved:
            # Only a changed config is checked, so an outage of the store's
            # DNS cannot block renaming or disabling it. The check may resolve
            # DNS, so it runs before the lock, which blocks reference writes.
            await backend.check_config(config)
            # Block concurrent reference creation between the checks and commit.
            await self.session.refresh(store, with_for_update=True)
            if parse_store_config(store) != saved:
                raise TracecatConflictError(
                    "The store changed while it was being saved. Try again."
                )
            backend.check_change(
                saved, config, references=await self._saved_references(store.id)
            )
            store.config = config.model_dump(mode="json")
        if params.access_token is not None:
            store.encrypted_access_token = _encrypt_access_token(params.access_token)
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
        await self._commit_removal()

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

        stmt = (
            insert(WorkspaceSecretStoreAuthorization)
            .values(
                organization_id=self.organization_id,
                workspace_id=workspace_id,
                store_id=store.id,
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "store_id"])
        )
        await self.session.execute(stmt)
        authorization = (
            await self.session.execute(
                select(WorkspaceSecretStoreAuthorization).where(
                    WorkspaceSecretStoreAuthorization.store_id == store.id,
                    WorkspaceSecretStoreAuthorization.workspace_id == workspace_id,
                )
            )
        ).scalar_one()
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
        await self.session.refresh(
            store, attribute_names=["all_workspaces"], with_for_update={"read": True}
        )
        if store.all_workspaces:
            raise TracecatConflictError(
                "Turn off all-workspace access before revoking individual workspaces."
            )
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
        await self._commit_removal()

    async def _commit_removal(self) -> None:
        """Turn a reference inserted after the preflight count into a conflict."""
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise TracecatConflictError(
                "Secret store or authorization is still referenced by workspace"
                " secrets. Remove those references first."
            ) from exc
