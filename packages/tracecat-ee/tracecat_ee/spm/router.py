"""EE SPM API router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from tracecat import config
from tracecat.auth.dependencies import OrgUserRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.pagination import CursorPaginationParams
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat_ee.spm.exceptions import (
    SpmAuthenticationError,
    SpmConflictError,
    SpmControlCatalogError,
    SpmError,
    SpmNotFoundError,
)
from tracecat_ee.spm.schemas import (
    SpmControlRead,
    SpmEndpointCreate,
    SpmEndpointCreateResponse,
    SpmEndpointInventoryListResponse,
    SpmEndpointListResponse,
    SpmEndpointRead,
    SpmEndpointSyncRequest,
    SpmEndpointSyncResponse,
    SpmFindingDecisionCreate,
    SpmFindingDecisionRead,
    SpmFindingListResponse,
    SpmFindingQueryParams,
    SpmFindingRead,
    SpmInventoryItemRead,
    SpmInventoryListResponse,
    SpmInventoryQueryParams,
    SpmInventoryTaxonomyRead,
    SpmResponseActionPreviewCreate,
    SpmResponseActionPreviewRead,
    SpmResponseActionRead,
)
from tracecat_ee.spm.service import SpmService, SpmSyncService
from tracecat_ee.spm.types import (
    SpmEnforcementAction,
    SpmHarness,
    SpmInventoryItemType,
    SpmInventorySourceType,
)

router = APIRouter(prefix="/spm", tags=["spm"])


def _spm_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, SpmError):
        if isinstance(exc, SpmAuthenticationError):
            status_code = status.HTTP_401_UNAUTHORIZED
        elif isinstance(exc, SpmNotFoundError):
            status_code = status.HTTP_404_NOT_FOUND
        elif isinstance(exc, SpmConflictError):
            status_code = status.HTTP_409_CONFLICT
        elif isinstance(exc, SpmControlCatalogError):
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        else:
            status_code = status.HTTP_400_BAD_REQUEST
        return HTTPException(status_code=status_code, detail=exc.to_detail())

    if isinstance(exc, EntitlementRequired):
        detail = {
            "code": "spm_entitlement_required",
            "message": str(exc),
            **(exc.detail if isinstance(exc.detail, dict) else {}),
        }
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    if isinstance(exc, TracecatNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "spm_not_found", "message": str(exc)},
        )

    if isinstance(exc, TracecatValidationError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "spm_validation_failed", "message": str(exc)},
        )

    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "spm_internal_error", "message": "Internal SPM error."},
    )


def _pagination_params(
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
) -> CursorPaginationParams:
    return CursorPaginationParams.model_construct(limit=limit, cursor=cursor)


def _inventory_query_params(
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    harness: SpmHarness | None = Query(default=None),
    endpoint_id: uuid.UUID | None = Query(default=None),
    item_type: SpmInventoryItemType | None = Query(default=None),
    source_type: SpmInventorySourceType | None = Query(default=None),
) -> SpmInventoryQueryParams:
    return SpmInventoryQueryParams.model_validate(
        {
            "limit": limit,
            "cursor": cursor,
            "harness": harness,
            "endpoint_id": endpoint_id,
            "item_type": item_type,
            "source_type": source_type,
        }
    )


def _finding_query_params(
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    endpoint_id: uuid.UUID | None = Query(default=None),
    control_id: str | None = Query(default=None),
) -> SpmFindingQueryParams:
    return SpmFindingQueryParams.model_validate(
        {
            "limit": limit,
            "cursor": cursor,
            "endpoint_id": endpoint_id,
            "control_id": control_id,
        }
    )


async def _require_spm_entitlement(
    role: OrgUserRole,
    session: AsyncDBSession,
) -> None:
    try:
        await check_entitlement(session, role, Entitlement.SPM)
    except EntitlementRequired as exc:
        raise _spm_http_exception(exc) from exc


def _parse_bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise SpmAuthenticationError(
            "Missing Authorization header.",
            code="spm_authorization_missing",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise SpmAuthenticationError(
            "Invalid Authorization header.",
            code="spm_authorization_invalid",
        )
    return token


@router.get(
    "/actions",
    response_model=list[SpmResponseActionRead],
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def list_spm_response_actions(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> list[SpmResponseActionRead]:
    service = SpmService(session, role=role)
    return await service.list_response_actions()


@router.get(
    "/actions/{action}",
    response_model=SpmResponseActionRead,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def get_spm_response_action(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    action: SpmEnforcementAction,
) -> SpmResponseActionRead:
    service = SpmService(session, role=role)
    try:
        return await service.get_response_action(action.value)
    except (SpmError, TracecatNotFoundError) as exc:
        raise _spm_http_exception(exc) from exc


@router.get(
    "/controls",
    response_model=list[SpmControlRead],
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def list_spm_controls(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> list[SpmControlRead]:
    service = SpmService(session, role=role)
    return await service.list_controls()


@router.get(
    "/controls/{control_id}",
    response_model=SpmControlRead,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def get_spm_control(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    control_id: str,
) -> SpmControlRead:
    service = SpmService(session, role=role)
    try:
        return await service.get_control(control_id)
    except (SpmError, TracecatNotFoundError) as exc:
        raise _spm_http_exception(exc) from exc


@router.get(
    "/endpoints",
    response_model=SpmEndpointListResponse,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def list_spm_endpoints(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    pagination: CursorPaginationParams = Depends(_pagination_params),
) -> SpmEndpointListResponse:
    service = SpmService(session, role=role)
    return await service.list_endpoints(pagination)


@router.post(
    "/endpoints",
    response_model=SpmEndpointCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:update")
async def create_spm_endpoint(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: SpmEndpointCreate,
) -> SpmEndpointCreateResponse:
    service = SpmService(session, role=role)
    return await service.create_endpoint(params)


@router.delete(
    "/endpoints/{endpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:update")
async def delete_spm_endpoint(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    endpoint_id: uuid.UUID,
) -> None:
    service = SpmService(session, role=role)
    try:
        await service.delete_pending_endpoint(endpoint_id)
    except (SpmError, TracecatNotFoundError, TracecatValidationError) as exc:
        raise _spm_http_exception(exc) from exc


@router.get(
    "/endpoints/{endpoint_id}",
    response_model=SpmEndpointRead,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def get_spm_endpoint(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    endpoint_id: uuid.UUID,
) -> SpmEndpointRead:
    service = SpmService(session, role=role)
    try:
        return await service.get_endpoint(endpoint_id)
    except (SpmError, TracecatNotFoundError) as exc:
        raise _spm_http_exception(exc) from exc


@router.get(
    "/inventory/taxonomy",
    response_model=SpmInventoryTaxonomyRead,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def get_spm_inventory_taxonomy(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> SpmInventoryTaxonomyRead:
    service = SpmService(session, role=role)
    return await service.get_inventory_taxonomy()


@router.get(
    "/endpoints/{endpoint_id}/inventory",
    response_model=SpmEndpointInventoryListResponse,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def list_spm_endpoint_inventory(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    endpoint_id: uuid.UUID,
    pagination: CursorPaginationParams = Depends(_pagination_params),
) -> SpmEndpointInventoryListResponse:
    service = SpmService(session, role=role)
    try:
        return await service.list_endpoint_inventory(endpoint_id, pagination)
    except (SpmError, TracecatNotFoundError) as exc:
        raise _spm_http_exception(exc) from exc


@router.get(
    "/inventory",
    response_model=SpmInventoryListResponse,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def list_spm_inventory(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: SpmInventoryQueryParams = Depends(_inventory_query_params),
) -> SpmInventoryListResponse:
    service = SpmService(session, role=role)
    return await service.list_inventory(params)


@router.get(
    "/inventory/{inventory_item_id}",
    response_model=SpmInventoryItemRead,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def get_spm_inventory_item(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    inventory_item_id: uuid.UUID,
) -> SpmInventoryItemRead:
    service = SpmService(session, role=role)
    try:
        return await service.get_inventory_item(inventory_item_id)
    except (SpmError, TracecatNotFoundError) as exc:
        raise _spm_http_exception(exc) from exc


@router.get(
    "/findings",
    response_model=SpmFindingListResponse,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def list_spm_findings(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: SpmFindingQueryParams = Depends(_finding_query_params),
) -> SpmFindingListResponse:
    service = SpmService(session, role=role)
    return await service.list_findings(params)


@router.get(
    "/findings/{finding_id}",
    response_model=SpmFindingRead,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def get_spm_finding(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    finding_id: uuid.UUID,
) -> SpmFindingRead:
    service = SpmService(session, role=role)
    try:
        return await service.get_finding(finding_id)
    except (SpmError, TracecatNotFoundError) as exc:
        raise _spm_http_exception(exc) from exc


@router.post(
    "/findings/{finding_id}/decisions",
    response_model=SpmFindingDecisionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:update")
async def create_spm_finding_decision(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    finding_id: uuid.UUID,
    params: SpmFindingDecisionCreate,
) -> SpmFindingDecisionRead:
    service = SpmService(session, role=role)
    try:
        return await service.create_finding_decision(finding_id, params)
    except (SpmError, TracecatNotFoundError) as exc:
        raise _spm_http_exception(exc) from exc


@router.post(
    "/findings/{finding_id}/action-preview",
    response_model=SpmResponseActionPreviewRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:update")
async def create_spm_response_action_preview(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    finding_id: uuid.UUID,
    params: SpmResponseActionPreviewCreate,
) -> SpmResponseActionPreviewRead:
    service = SpmService(session, role=role)
    try:
        return await service.create_response_action_preview(finding_id, params)
    except (SpmError, TracecatNotFoundError) as exc:
        raise _spm_http_exception(exc) from exc


@router.get(
    "/action-previews/{preview_id}",
    response_model=SpmResponseActionPreviewRead,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def get_spm_response_action_preview(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    preview_id: uuid.UUID,
) -> SpmResponseActionPreviewRead:
    service = SpmService(session, role=role)
    try:
        return await service.get_response_action_preview(preview_id)
    except (SpmError, TracecatNotFoundError) as exc:
        raise _spm_http_exception(exc) from exc


@router.post(
    "/endpoints/{endpoint_id}/sync",
    response_model=SpmEndpointSyncResponse,
)
async def sync_spm_endpoint(
    *,
    session: AsyncDBSession,
    endpoint_id: uuid.UUID,
    params: SpmEndpointSyncRequest,
    authorization: str | None = Header(default=None),
) -> SpmEndpointSyncResponse:
    service = SpmSyncService(session)
    try:
        return await service.sync_endpoint(
            endpoint_id=endpoint_id,
            bearer_token=_parse_bearer_token(authorization),
            params=params,
        )
    except (SpmError, TracecatNotFoundError, EntitlementRequired) as exc:
        raise _spm_http_exception(exc) from exc
