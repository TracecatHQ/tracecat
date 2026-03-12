from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.api_keys.schemas import (
    ApiKeyCreate,
    ApiKeyScopeList,
    ApiKeyScopeRead,
    ApiKeyUpdate,
    OrganizationApiKeyCreateResponse,
    OrganizationApiKeyRead,
    WorkspaceApiKeyCreateResponse,
    WorkspaceApiKeyRead,
)
from tracecat.api_keys.service import OrganizationApiKeyService, WorkspaceApiKeyService
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

org_router = APIRouter(prefix="/organization/api-keys", tags=["api_keys"])
workspace_router = APIRouter(
    prefix="/workspaces/{workspace_id}/api-keys", tags=["api_keys"]
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
    logger.exception("Unhandled API key route error")
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Internal server error",
    )


@org_router.get("", response_model=CursorPaginatedResponse[OrganizationApiKeyRead])
@require_scope("org:api_key:read")
async def list_organization_api_keys(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[OrganizationApiKeyRead]:
    service = OrganizationApiKeyService(session, role=role)
    try:
        page = await service.list_keys(
            CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return CursorPaginatedResponse(
        items=OrganizationApiKeyRead.list_adapter().validate_python(page.items),
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
        has_more=page.has_more,
        has_previous=page.has_previous,
        total_estimate=page.total_estimate,
    )


@org_router.get("/scopes", response_model=ApiKeyScopeList)
@require_scope(
    "org:api_key:read",
    "org:api_key:create",
    "org:api_key:update",
    require_all=False,
)
async def list_organization_api_key_scopes(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
) -> ApiKeyScopeList:
    service = OrganizationApiKeyService(session, role=role)
    scopes = await service.list_assignable_scopes()
    return ApiKeyScopeList(items=ApiKeyScopeRead.list_adapter().validate_python(scopes))


@org_router.post(
    "",
    response_model=OrganizationApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization_api_key(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    params: ApiKeyCreate,
) -> OrganizationApiKeyCreateResponse:
    service = OrganizationApiKeyService(session, role=role)
    try:
        api_key, raw_key = await service.create_key(
            name=params.name,
            description=params.description,
            scope_ids=params.scope_ids,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return OrganizationApiKeyCreateResponse(
        api_key=raw_key,
        key=OrganizationApiKeyRead.model_validate(api_key),
    )


@org_router.get("/{api_key_id}", response_model=OrganizationApiKeyRead)
@require_scope("org:api_key:read")
async def get_organization_api_key(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    api_key_id: UUID,
) -> OrganizationApiKeyRead:
    service = OrganizationApiKeyService(session, role=role)
    try:
        return OrganizationApiKeyRead.model_validate(await service.get_key(api_key_id))
    except Exception as exc:
        raise _translate_error(exc) from exc


@org_router.patch("/{api_key_id}", response_model=OrganizationApiKeyRead)
async def update_organization_api_key(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    api_key_id: UUID,
    params: ApiKeyUpdate,
) -> OrganizationApiKeyRead:
    service = OrganizationApiKeyService(session, role=role)
    try:
        api_key = await service.update_key(
            api_key_id,
            name=params.name,
            description=params.description,
            description_provided="description" in params.model_fields_set,
            scope_ids=params.scope_ids,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return OrganizationApiKeyRead.model_validate(api_key)


@org_router.post("/{api_key_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_organization_api_key(
    *,
    role: OrgUserOnlyRole,
    session: AsyncDBSession,
    api_key_id: UUID,
) -> None:
    service = OrganizationApiKeyService(session, role=role)
    try:
        await service.revoke_key(api_key_id)
    except Exception as exc:
        raise _translate_error(exc) from exc


@workspace_router.get("", response_model=CursorPaginatedResponse[WorkspaceApiKeyRead])
@require_scope("workspace:api_key:read")
async def list_workspace_api_keys(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[WorkspaceApiKeyRead]:
    service = WorkspaceApiKeyService(session, role=role)
    try:
        page = await service.list_keys(
            CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return CursorPaginatedResponse(
        items=WorkspaceApiKeyRead.list_adapter().validate_python(page.items),
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
        has_more=page.has_more,
        has_previous=page.has_previous,
        total_estimate=page.total_estimate,
    )


@workspace_router.get("/scopes", response_model=ApiKeyScopeList)
@require_scope(
    "workspace:api_key:read",
    "workspace:api_key:create",
    "workspace:api_key:update",
    require_all=False,
)
async def list_workspace_api_key_scopes(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
) -> ApiKeyScopeList:
    service = WorkspaceApiKeyService(session, role=role)
    scopes = await service.list_assignable_scopes()
    return ApiKeyScopeList(items=ApiKeyScopeRead.list_adapter().validate_python(scopes))


@workspace_router.post(
    "",
    response_model=WorkspaceApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_api_key(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    params: ApiKeyCreate,
) -> WorkspaceApiKeyCreateResponse:
    service = WorkspaceApiKeyService(session, role=role)
    try:
        api_key, raw_key = await service.create_key(
            name=params.name,
            description=params.description,
            scope_ids=params.scope_ids,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return WorkspaceApiKeyCreateResponse(
        api_key=raw_key,
        key=WorkspaceApiKeyRead.model_validate(api_key),
    )


@workspace_router.get("/{api_key_id}", response_model=WorkspaceApiKeyRead)
@require_scope("workspace:api_key:read")
async def get_workspace_api_key(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    api_key_id: UUID,
) -> WorkspaceApiKeyRead:
    service = WorkspaceApiKeyService(session, role=role)
    try:
        return WorkspaceApiKeyRead.model_validate(await service.get_key(api_key_id))
    except Exception as exc:
        raise _translate_error(exc) from exc


@workspace_router.patch("/{api_key_id}", response_model=WorkspaceApiKeyRead)
async def update_workspace_api_key(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    api_key_id: UUID,
    params: ApiKeyUpdate,
) -> WorkspaceApiKeyRead:
    service = WorkspaceApiKeyService(session, role=role)
    try:
        api_key = await service.update_key(
            api_key_id,
            name=params.name,
            description=params.description,
            description_provided="description" in params.model_fields_set,
            scope_ids=params.scope_ids,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
    return WorkspaceApiKeyRead.model_validate(api_key)


@workspace_router.post("/{api_key_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_workspace_api_key(
    *,
    role: WorkspaceUserOnlyInPath,
    session: AsyncDBSession,
    api_key_id: UUID,
) -> None:
    service = WorkspaceApiKeyService(session, role=role)
    try:
        await service.revoke_key(api_key_id)
    except Exception as exc:
        raise _translate_error(exc) from exc
