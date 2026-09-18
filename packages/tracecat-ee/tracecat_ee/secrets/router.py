"""EE external secret store API routers.

Organization admins manage stores and workspace grants; workspace members
create and verify secrets whose values live in an external store.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from temporalio.client import WorkflowFailureError

from tracecat import config
from tracecat.auth.dependencies import OrgActorRole, WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import OrganizationSecretStore
from tracecat.dsl.client import get_temporal_client
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.identifiers import WorkspaceID
from tracecat.pagination import Page, PageParams, PaginationError
from tracecat.secrets.dependencies import AnySecretIDPath
from tracecat.secrets.schemas import (
    AwsSecretReferenceCreate,
    AwsSecretReferenceUpdate,
    SecretReferenceCheckRequest,
    SecretReferenceCheckResult,
    SecretStoreAuthorizationCreate,
    SecretStoreAuthorizationRead,
    SecretStoreCreate,
    SecretStoreRead,
    SecretStoreUpdate,
    WorkspaceSecretStoreRead,
)
from tracecat.secrets.service import is_external_reference
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat_ee.secrets.service import ExternalSecretsService
from tracecat_ee.secrets.store_service import SecretStoresService
from tracecat_ee.secrets.workflow import (
    SecretReferenceCheckWorkflow,
)


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


@router.get("/stores", response_model=Page[WorkspaceSecretStoreRead])
@require_scope("secret:read")
async def list_authorized_secret_stores(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    limit: int = Query(
        config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(None, max_length=8192),
) -> Page[WorkspaceSecretStoreRead]:
    """List external secret stores this workspace is authorized to reference."""
    service = ExternalSecretsService(session, role=role)
    try:
        stores = await service.list_authorized_stores(
            PageParams(limit=limit, cursor=cursor)
        )
    except PaginationError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return Page(
        items=[WorkspaceSecretStoreRead.from_database(store) for store in stores.items],
        next_cursor=stores.next_cursor,
        prev_cursor=stores.prev_cursor,
    )


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
        if not is_external_reference(secret):
            raise ValueError("Secret is not backed by AWS Secrets Manager.")
        # Only identifiers and actor context cross Temporal; values stay on the executor.
        await session.commit()
        client = await get_temporal_client()
        return await client.execute_workflow(
            SecretReferenceCheckWorkflow.run,
            SecretReferenceCheckRequest(role=role, secret_id=secret_id),
            id=f"secret-reference-check-{uuid4()}",
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            execution_timeout=timedelta(seconds=60),
        )
    except WorkflowFailureError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Secret reference check could not complete. Try again.",
        ) from exc
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
    limit: int = Query(
        config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(None, max_length=8192),
) -> Page[SecretStoreRead]:
    """List external secret stores owned by the organization."""
    service = SecretStoresService(session, role=role)
    try:
        stores = await service.list_stores(PageParams(limit=limit, cursor=cursor))
    except PaginationError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    counts = await service.count_references([store.id for store in stores.items])
    return Page(
        items=[
            SecretStoreRead.from_database(
                store,
                authorized_workspace_ids=[a.workspace_id for a in store.authorizations],
                reference_count=counts.get(store.id, 0),
                tracecat_aws_account_id=config.TRACECAT__AWS_ASSUME_ROLE_ACCOUNT_ID
                or None,
                tracecat_aws_principal_arn=config.TRACECAT__AWS_ASSUME_ROLE_PRINCIPAL_ARN
                or None,
            )
            for store in stores.items
        ],
        next_cursor=stores.next_cursor,
        prev_cursor=stores.prev_cursor,
    )


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
