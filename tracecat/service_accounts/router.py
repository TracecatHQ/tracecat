from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.credentials import RoleACL
from tracecat.auth.dependencies import OrgUserOnlyRole
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.logger import logger
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams
from tracecat.service_accounts.schemas import (
    ServiceAccountApiKeyCreate,
    ServiceAccountApiKeyRead,
    ServiceAccountCreate,
    ServiceAccountCreateResponse,
    ServiceAccountRead,
    ServiceAccountScopeList,
    ServiceAccountScopeRead,
    ServiceAccountUpdate,
)
from tracecat.service_accounts.service import (
    OrganizationServiceAccountService,
    WorkspaceServiceAccountService,
    get_active_service_account_key,
    get_service_account_last_used_at,
)

org_router = APIRouter(
    prefix="/organization/service-accounts", tags=["service_accounts"]
)
workspace_router = APIRouter(
    prefix="/workspaces/{workspace_id}/service-accounts", tags=["service_accounts"]
)

WorkspaceUserOnlyInPath = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        allow_api_key=False,
        require_workspace="yes",
        workspace_id_in_path=True,
    ),
]


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, TracecatNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, TracecatAuthorizationError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, TracecatValidationError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    logger.exception("Unhandled service account route error")
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Internal server error",
    )


def _serialize_api_key(api_key) -> ServiceAccountApiKeyRead | None:
    if api_key is None:
        return None
    return ServiceAccountApiKeyRead.model_validate(api_key)


def _serialize_service_account(service_account) -> ServiceAccountRead:
    active_key = get_active_service_account_key(service_account)
    return ServiceAccountRead(
        id=service_account.id,
        organization_id=service_account.organization_id,
        workspace_id=service_account.workspace_id,
        owner_user_id=service_account.owner_user_id,
        name=service_account.name,
        description=service_account.description,
        disabled_at=service_account.disabled_at,
        last_used_at=get_service_account_last_used_at(service_account),
        created_at=service_account.created_at,
        updated_at=service_account.updated_at,
        scopes=ServiceAccountScopeRead.list_adapter().validate_python(
            service_account.scopes
        ),
        api_key=_serialize_api_key(active_key),
    )


@org_router.get("", response_model=CursorPaginatedResponse[ServiceAccountRead])
@require_scope("org:service_account:read")
async def list_organization_service_accounts(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[ServiceAccountRead]:
    service = OrganizationServiceAccountService(session, role=role)
    try:
        page = await service.list_service_accounts(
            CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return CursorPaginatedResponse(
        items=[_serialize_service_account(item) for item in page.items],
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
        has_more=page.has_more,
        has_previous=page.has_previous,
        total_estimate=page.total_estimate,
    )


@org_router.get("/scopes", response_model=ServiceAccountScopeList)
@require_scope(
    "org:service_account:read",
    "org:service_account:create",
    "org:service_account:update",
    require_all=False,
)
async def list_organization_service_account_scopes(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
) -> ServiceAccountScopeList:
    service = OrganizationServiceAccountService(session, role=role)
    scopes = await service.list_assignable_scopes()
    return ServiceAccountScopeList(
        items=ServiceAccountScopeRead.list_adapter().validate_python(scopes)
    )


@org_router.post(
    "",
    response_model=ServiceAccountCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
@require_scope("org:service_account:create")
async def create_organization_service_account(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    params: ServiceAccountCreate,
) -> ServiceAccountCreateResponse:
    service = OrganizationServiceAccountService(session, role=role)
    try:
        service_account, raw_key = await service.create_service_account(
            name=params.name,
            description=params.description,
            scope_ids=params.scope_ids,
            initial_key_name=params.initial_key_name,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return ServiceAccountCreateResponse(
        api_key=raw_key,
        service_account=_serialize_service_account(service_account),
    )


@org_router.get("/{service_account_id}", response_model=ServiceAccountRead)
@require_scope("org:service_account:read")
async def get_organization_service_account(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    service_account_id: UUID,
) -> ServiceAccountRead:
    service = OrganizationServiceAccountService(session, role=role)
    try:
        return _serialize_service_account(
            await service.get_service_account(service_account_id)
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@org_router.patch("/{service_account_id}", response_model=ServiceAccountRead)
@require_scope("org:service_account:update")
async def update_organization_service_account(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    service_account_id: UUID,
    params: ServiceAccountUpdate,
) -> ServiceAccountRead:
    service = OrganizationServiceAccountService(session, role=role)
    try:
        service_account = await service.update_service_account(
            service_account_id,
            name=params.name,
            description=params.description,
            description_provided="description" in params.model_fields_set,
            scope_ids=params.scope_ids,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return _serialize_service_account(service_account)


@org_router.post(
    "/{service_account_id}/disable", status_code=status.HTTP_204_NO_CONTENT
)
@require_scope("org:service_account:disable")
async def disable_organization_service_account(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    service_account_id: UUID,
) -> None:
    service = OrganizationServiceAccountService(session, role=role)
    try:
        await service.disable_service_account(service_account_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@org_router.post("/{service_account_id}/enable", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:service_account:disable")
async def enable_organization_service_account(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    service_account_id: UUID,
) -> None:
    service = OrganizationServiceAccountService(session, role=role)
    try:
        await service.enable_service_account(service_account_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@org_router.post(
    "/{service_account_id}/regenerate-key",
    response_model=ServiceAccountCreateResponse,
)
@require_scope("org:service_account:update")
async def regenerate_organization_service_account_key(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    service_account_id: UUID,
    params: ServiceAccountApiKeyCreate,
) -> ServiceAccountCreateResponse:
    service = OrganizationServiceAccountService(session, role=role)
    try:
        service_account, raw_key = await service.regenerate_key(
            service_account_id,
            name=params.name,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return ServiceAccountCreateResponse(
        api_key=raw_key,
        service_account=_serialize_service_account(service_account),
    )


@org_router.post(
    "/{service_account_id}/revoke-key", status_code=status.HTTP_204_NO_CONTENT
)
@require_scope("org:service_account:update")
async def revoke_organization_service_account_key(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    service_account_id: UUID,
) -> None:
    service = OrganizationServiceAccountService(session, role=role)
    try:
        await service.revoke_key(service_account_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@workspace_router.get("", response_model=CursorPaginatedResponse[ServiceAccountRead])
@require_scope("workspace:service_account:read")
async def list_workspace_service_accounts(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[ServiceAccountRead]:
    service = WorkspaceServiceAccountService(session, role=role)
    try:
        page = await service.list_service_accounts(
            CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return CursorPaginatedResponse(
        items=[_serialize_service_account(item) for item in page.items],
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
        has_more=page.has_more,
        has_previous=page.has_previous,
        total_estimate=page.total_estimate,
    )


@workspace_router.get("/scopes", response_model=ServiceAccountScopeList)
@require_scope(
    "workspace:service_account:read",
    "workspace:service_account:create",
    "workspace:service_account:update",
    require_all=False,
)
async def list_workspace_service_account_scopes(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
) -> ServiceAccountScopeList:
    service = WorkspaceServiceAccountService(session, role=role)
    scopes = await service.list_assignable_scopes()
    return ServiceAccountScopeList(
        items=ServiceAccountScopeRead.list_adapter().validate_python(scopes)
    )


@workspace_router.post(
    "",
    response_model=ServiceAccountCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
@require_scope("workspace:service_account:create")
async def create_workspace_service_account(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    params: ServiceAccountCreate,
) -> ServiceAccountCreateResponse:
    service = WorkspaceServiceAccountService(session, role=role)
    try:
        service_account, raw_key = await service.create_service_account(
            name=params.name,
            description=params.description,
            scope_ids=params.scope_ids,
            initial_key_name=params.initial_key_name,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return ServiceAccountCreateResponse(
        api_key=raw_key,
        service_account=_serialize_service_account(service_account),
    )


@workspace_router.get("/{service_account_id}", response_model=ServiceAccountRead)
@require_scope("workspace:service_account:read")
async def get_workspace_service_account(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    service_account_id: UUID,
) -> ServiceAccountRead:
    service = WorkspaceServiceAccountService(session, role=role)
    try:
        return _serialize_service_account(
            await service.get_service_account(service_account_id)
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@workspace_router.patch("/{service_account_id}", response_model=ServiceAccountRead)
@require_scope("workspace:service_account:update")
async def update_workspace_service_account(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    service_account_id: UUID,
    params: ServiceAccountUpdate,
) -> ServiceAccountRead:
    service = WorkspaceServiceAccountService(session, role=role)
    try:
        service_account = await service.update_service_account(
            service_account_id,
            name=params.name,
            description=params.description,
            description_provided="description" in params.model_fields_set,
            scope_ids=params.scope_ids,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return _serialize_service_account(service_account)


@workspace_router.post(
    "/{service_account_id}/disable",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("workspace:service_account:disable")
async def disable_workspace_service_account(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    service_account_id: UUID,
) -> None:
    service = WorkspaceServiceAccountService(session, role=role)
    try:
        await service.disable_service_account(service_account_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@workspace_router.post(
    "/{service_account_id}/enable",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("workspace:service_account:disable")
async def enable_workspace_service_account(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    service_account_id: UUID,
) -> None:
    service = WorkspaceServiceAccountService(session, role=role)
    try:
        await service.enable_service_account(service_account_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@workspace_router.post(
    "/{service_account_id}/regenerate-key",
    response_model=ServiceAccountCreateResponse,
)
@require_scope("workspace:service_account:update")
async def regenerate_workspace_service_account_key(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    service_account_id: UUID,
    params: ServiceAccountApiKeyCreate,
) -> ServiceAccountCreateResponse:
    service = WorkspaceServiceAccountService(session, role=role)
    try:
        service_account, raw_key = await service.regenerate_key(
            service_account_id,
            name=params.name,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return ServiceAccountCreateResponse(
        api_key=raw_key,
        service_account=_serialize_service_account(service_account),
    )


@workspace_router.post(
    "/{service_account_id}/revoke-key",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("workspace:service_account:update")
async def revoke_workspace_service_account_key(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    service_account_id: UUID,
) -> None:
    service = WorkspaceServiceAccountService(session, role=role)
    try:
        await service.revoke_key(service_account_id)
    except Exception as exc:
        raise _translate_error(exc) from exc
