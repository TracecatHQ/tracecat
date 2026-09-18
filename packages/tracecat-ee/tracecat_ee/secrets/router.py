"""EE external secret store API routers.

Organization admins manage stores and workspace grants; workspace members
create and verify secrets whose values live in an external store.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError

from tracecat import config
from tracecat.auth.dependencies import OrgActorRole, WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import OrganizationSecretStore
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.identifiers import WorkspaceID
from tracecat.secrets.dependencies import AnySecretIDPath
from tracecat.secrets.schemas import (
    AwsSecretReferenceCreate,
    AwsSecretReferenceUpdate,
    SecretReferenceCheckResult,
    SecretStoreAuthorizationCreate,
    SecretStoreAuthorizationRead,
    SecretStoreCreate,
    SecretStoreRead,
    SecretStoreUpdate,
    WorkspaceSecretStoreRead,
)
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat_ee.secrets.service import ExternalSecretsService
from tracecat_ee.secrets.store_service import SecretStoresService


async def _require_org_entitlement(
    role: OrgActorRole,
    session: AsyncDBSession,
) -> None:
    await check_entitlement(session, role, Entitlement.EXTERNAL_SECRET_STORES)


async def _require_workspace_entitlement(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> None:
    await check_entitlement(session, role, Entitlement.EXTERNAL_SECRET_STORES)


router = APIRouter(
    prefix="/secrets",
    tags=["secrets"],
    dependencies=[Depends(_require_workspace_entitlement)],
)
org_store_router = APIRouter(
    prefix="/organization/secret-stores",
    tags=["organization-secret-stores"],
    dependencies=[Depends(_require_org_entitlement)],
)


async def _serialize_store_read(
    service: SecretStoresService, store: OrganizationSecretStore
) -> SecretStoreRead:
    counts = await service.count_references([store.id])
    return SecretStoreRead.from_database(
        store,
        authorized_workspace_ids=[a.workspace_id for a in store.authorizations],
        reference_count=counts.get(store.id, 0),
        tracecat_aws_account_id=config.TRACECAT__AWS_ASSUME_ROLE_ACCOUNT_ID or None,
        tracecat_aws_principal_arn=config.TRACECAT__AWS_ASSUME_ROLE_PRINCIPAL_ARN
        or None,
    )


# === Workspace external secret references ===


@router.get("/stores", response_model=list[WorkspaceSecretStoreRead])
@require_scope("secret:read")
async def list_authorized_secret_stores(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> list[WorkspaceSecretStoreRead]:
    """List external secret stores this workspace is authorized to reference."""
    service = ExternalSecretsService(session, role=role)
    stores = await service.list_authorized_stores()
    return [WorkspaceSecretStoreRead.from_database(store) for store in stores]


@router.post("/aws", status_code=status.HTTP_201_CREATED)
@require_scope("secret:create")
async def create_aws_secret_reference(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: AwsSecretReferenceCreate,
) -> None:
    """Create a custom secret whose values live in AWS Secrets Manager."""
    service = ExternalSecretsService(session, role=role)
    try:
        await service.create_aws_secret_reference(params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
        ) from e


@router.post("/aws/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("secret:update")
async def update_aws_secret_reference(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    secret_id: AnySecretIDPath,
    params: AwsSecretReferenceUpdate,
) -> None:
    """Update the reference or key mapping of an AWS-backed secret."""
    service = ExternalSecretsService(session, role=role)
    try:
        secret = await service.get_secret(secret_id)
        await service.update_aws_secret_reference(secret, params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
        ) from e


@router.post("/aws/{secret_id}/check", response_model=SecretReferenceCheckResult)
@require_scope("secret:read")
async def check_aws_secret_reference(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    secret_id: AnySecretIDPath,
) -> SecretReferenceCheckResult:
    """Verify a saved AWS-backed reference resolves. Values are never returned."""
    service = ExternalSecretsService(session, role=role)
    try:
        secret = await service.get_secret(secret_id)
        return await service.check_aws_secret_reference(secret)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        ) from e


# === Organization secret stores ===


@org_store_router.get("")
@require_scope("org:secret:read")
async def list_secret_stores(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
) -> list[SecretStoreRead]:
    """List external secret stores owned by the organization."""
    service = SecretStoresService(session, role=role)
    stores = await service.list_stores()
    counts = await service.count_references([store.id for store in stores])
    return [
        SecretStoreRead.from_database(
            store,
            authorized_workspace_ids=[a.workspace_id for a in store.authorizations],
            reference_count=counts.get(store.id, 0),
            tracecat_aws_account_id=config.TRACECAT__AWS_ASSUME_ROLE_ACCOUNT_ID or None,
            tracecat_aws_principal_arn=config.TRACECAT__AWS_ASSUME_ROLE_PRINCIPAL_ARN
            or None,
        )
        for store in stores
    ]


@org_store_router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("org:secret:create")
async def create_secret_store(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    params: SecretStoreCreate,
) -> SecretStoreRead:
    """Create a store. The AssumeRole external ID is generated server-side."""
    service = SecretStoresService(session, role=role)
    try:
        store = await service.create_store(params)
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A secret store with this name already exists",
        ) from e
    return await _serialize_store_read(service, store)


@org_store_router.get("/{store_id}")
@require_scope("org:secret:read")
async def get_secret_store(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    store_id: UUID,
) -> SecretStoreRead:
    """Get a store, including its persisted trust-policy inputs."""
    service = SecretStoresService(session, role=role)
    try:
        store = await service.get_store(store_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret store not found"
        ) from e
    return await _serialize_store_read(service, store)


@org_store_router.patch("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:secret:update")
async def update_secret_store(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    store_id: UUID,
    params: SecretStoreUpdate,
) -> None:
    """Update store metadata. The external ID never changes."""
    service = SecretStoresService(session, role=role)
    try:
        store = await service.get_store(store_id)
        await service.update_store(store, params)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret store not found"
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A secret store with this name already exists",
        ) from e


@org_store_router.delete("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:secret:delete")
async def delete_secret_store(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    store_id: UUID,
) -> None:
    """Delete a store. Rejected while workspace secrets still reference it."""
    service = SecretStoresService(session, role=role)
    try:
        store = await service.get_store(store_id)
        await service.delete_store(store)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret store not found"
        ) from e
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@org_store_router.post(
    "/{store_id}/authorizations", status_code=status.HTTP_201_CREATED
)
@require_scope("org:secret:update")
async def authorize_secret_store_workspace(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    store_id: UUID,
    params: SecretStoreAuthorizationCreate,
) -> SecretStoreAuthorizationRead:
    """Authorize a workspace to reference this store."""
    service = SecretStoresService(session, role=role)
    try:
        store = await service.get_store(store_id)
        authorization = await service.authorize_workspace(store, params.workspace_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return SecretStoreAuthorizationRead.from_database(authorization)


@org_store_router.delete(
    "/{store_id}/authorizations/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("org:secret:update")
async def revoke_secret_store_workspace(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    store_id: UUID,
    workspace_id: WorkspaceID,
) -> None:
    """Revoke a workspace authorization. Rejected while references remain."""
    service = SecretStoresService(session, role=role)
    try:
        store = await service.get_store(store_id)
        await service.revoke_workspace(store, workspace_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
