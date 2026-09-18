"""Workspace secrets whose values live in an external secret store."""

from __future__ import annotations

import uuid

from asyncpg import ForeignKeyViolationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tracecat.audit.logger import audit_log
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    OrganizationSecretStore,
    Secret,
    WorkspaceSecretStoreAuthorization,
)
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.pagination import Page, PageParams, paginate
from tracecat.secrets.enums import (
    AwsSecretResolutionErrorCode,
    SecretSource,
    SecretType,
)
from tracecat.secrets.schemas import (
    AwsSecretReferenceCreate,
    AwsSecretReferenceUpdate,
    SecretReferenceCheckResult,
)
from tracecat.secrets.service import (
    SecretsService,
    build_external_secret_reference,
    is_external_reference,
)
from tracecat_ee.secrets.backends import get_backend, parse_store_config


class ExternalSecretsService(SecretsService):
    """Create, update, and verify workspace secrets backed by an external store."""

    service_name = "external_secrets"

    async def list_authorized_stores(
        self, page: PageParams
    ) -> Page[OrganizationSecretStore]:
        """List external stores the current workspace may reference."""
        workspace_id = self._require_workspace_id()
        stmt = (
            select(OrganizationSecretStore)
            .join(
                WorkspaceSecretStoreAuthorization,
                WorkspaceSecretStoreAuthorization.store_id
                == OrganizationSecretStore.id,
            )
            .where(
                WorkspaceSecretStoreAuthorization.workspace_id == workspace_id,
                OrganizationSecretStore.organization_id == self.organization_id,
            )
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

    async def _get_authorized_store(
        self, store_id: uuid.UUID
    ) -> OrganizationSecretStore:
        workspace_id = self._require_workspace_id()
        stmt = (
            select(OrganizationSecretStore)
            .join(
                WorkspaceSecretStoreAuthorization,
                WorkspaceSecretStoreAuthorization.store_id
                == OrganizationSecretStore.id,
            )
            .where(
                WorkspaceSecretStoreAuthorization.workspace_id == workspace_id,
                OrganizationSecretStore.id == store_id,
            )
        )
        result = await self.session.execute(stmt)
        store = result.scalar_one_or_none()
        if store is None:
            raise TracecatAuthorizationError(
                "This workspace is not authorized to use the selected secret store."
            )
        return store

    @staticmethod
    def _validate_reference(
        store: OrganizationSecretStore, remote_reference: str
    ) -> None:
        backend = get_backend(store.provider)
        backend.validate_reference(parse_store_config(store), remote_reference)

    @require_scope("secret:create")
    @audit_log(resource_type="secret", action="create")
    async def create_aws_secret_reference(
        self, params: AwsSecretReferenceCreate
    ) -> Secret:
        """Create a custom workspace secret backed by AWS Secrets Manager.

        No remote value is fetched or stored; ``encrypted_keys`` holds an
        encrypted empty key list so the column contract is unchanged.
        """
        workspace_id = self._require_workspace_id()
        store = await self._get_authorized_store(params.store_id)
        self._validate_reference(store, params.remote_reference)
        secret = Secret(
            workspace_id=workspace_id,
            name=params.name,
            type=SecretType.CUSTOM,
            description=params.description,
            tags=params.tags,
            encrypted_keys=self.encrypt_keys([]),
            environment=params.environment,
            source=SecretSource.AWS_SECRETS_MANAGER,
            store_id=store.id,
            remote_reference=params.remote_reference,
            remote_key_mapping=params.key_mapping.model_dump(mode="json"),
        )
        self.session.add(secret)
        await self._commit_reference()
        return secret

    async def _commit_reference(self) -> None:
        """Commit, mapping a lost store authorization to an authorization error."""
        try:
            await self.session.commit()
        except IntegrityError as e:
            # Any FK failure on this commit means the grant or store vanished.
            cause = e.orig.__cause__ if e.orig else None
            if not isinstance(cause, ForeignKeyViolationError):
                raise
            await self.session.rollback()
            raise TracecatAuthorizationError(
                "This workspace is not authorized to use the selected secret store."
            ) from e

    @require_scope("secret:update")
    @audit_log(resource_type="secret", action="update")
    async def update_aws_secret_reference(
        self, secret: Secret, params: AwsSecretReferenceUpdate
    ) -> None:
        """Update the reference or mapping of an AWS-backed workspace secret."""
        if not is_external_reference(secret):
            raise ValueError("Secret is not backed by AWS Secrets Manager.")
        set_fields = params.model_dump(exclude_unset=True)
        set_fields.pop("store_id", None)
        set_fields.pop("remote_reference", None)
        set_fields.pop("key_mapping", None)

        effective_store_id = params.store_id or secret.store_id
        if effective_store_id is None:
            raise ValueError("A secret store is required.")
        store = await self._get_authorized_store(effective_store_id)
        effective_reference = params.remote_reference or secret.remote_reference
        if effective_reference is None:
            raise ValueError("A secret ARN is required.")
        self._validate_reference(store, effective_reference)

        secret.store_id = store.id
        secret.remote_reference = effective_reference
        if params.key_mapping is not None:
            secret.remote_key_mapping = params.key_mapping.model_dump(mode="json")
        for field, value in set_fields.items():
            setattr(secret, field, value)
        self.session.add(secret)
        await self._commit_reference()

    @require_scope("secret:read")
    async def check_aws_secret_reference(
        self, secret: Secret
    ) -> SecretReferenceCheckResult:
        """Verify an AWS-backed secret resolves. Never returns the value."""
        if not is_external_reference(secret):
            raise ValueError("Secret is not backed by AWS Secrets Manager.")
        await self.session.refresh(secret, attribute_names=["store"])
        reference = build_external_secret_reference(secret)
        backend = get_backend(reference.provider)
        # Release the DB session before the remote call.
        await self.session.commit()
        ok, error_code, aws_code, keys = await backend.check(reference)
        message: str | None = None
        if not ok:
            code = error_code or AwsSecretResolutionErrorCode.UNKNOWN
            message = f"Reference check failed: {code.value}"
            if aws_code:
                message += f" (AWS error code {aws_code})"
        return SecretReferenceCheckResult(
            ok=ok, error_code=error_code, message=message, resolved_keys=keys
        )
