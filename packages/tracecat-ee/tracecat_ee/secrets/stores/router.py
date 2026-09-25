"""Organization secret store API: store lifecycle and workspace grants."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from tracecat import config
from tracecat.auth.dependencies import OrgActorRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import OrganizationSecretStore
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.identifiers import WorkspaceID
from tracecat.pagination import Page, PageParams, PaginationError
from tracecat.secrets.schemas import (
    SecretStoreAuthorizationCreate,
    SecretStoreAuthorizationRead,
    SecretStoreCreate,
    SecretStoreRead,
    SecretStoreUpdate,
)
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat_ee.secrets.stores.service import SecretStoresService


async def _require_entitlement(role: OrgActorRole, session: AsyncDBSession) -> None:
    await check_entitlement(session, role, Entitlement.EXTERNAL_SECRET_STORES)


router = APIRouter(
    prefix="/organization/secret-stores",
    tags=["organization-secret-stores"],
    dependencies=[Depends(_require_entitlement)],
)


def _store_read(
    store: OrganizationSecretStore, counts: dict[UUID, int]
) -> SecretStoreRead:
    return SecretStoreRead.from_database(
        store,
        authorized_workspace_ids=[a.workspace_id for a in store.authorizations],
        reference_count=counts.get(store.id, 0),
        tracecat_aws_account_id=config.TRACECAT__AWS_ASSUME_ROLE_ACCOUNT_ID or None,
        tracecat_aws_principal_arn=config.TRACECAT__AWS_ASSUME_ROLE_PRINCIPAL_ARN
        or None,
    )


async def _serialize_store_read(
    service: SecretStoresService, store: OrganizationSecretStore
) -> SecretStoreRead:
    counts = await service.count_references([store.id])
    return _store_read(store, counts)


@router.get("")
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
        items=[_store_read(store, counts) for store in stores.items],
        next_cursor=stores.next_cursor,
        prev_cursor=stores.prev_cursor,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
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


@router.get("/{store_id}")
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


@router.patch("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
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
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret store not found"
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A secret store with this name already exists",
        ) from e


@router.delete("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
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


@router.post("/{store_id}/authorizations", status_code=status.HTTP_201_CREATED)
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


@router.delete(
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
