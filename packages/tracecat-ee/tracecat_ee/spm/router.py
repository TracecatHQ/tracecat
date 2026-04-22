"""EE SPM API router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from tracecat import config
from tracecat.auth.dependencies import OrgUserRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import EntitlementRequired, TracecatNotFoundError
from tracecat.pagination import CursorPaginationParams
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat_ee.spm.schemas import (
    SpmAssetListResponse,
    SpmAssetRead,
    SpmControlRead,
    SpmEndpointCreate,
    SpmEndpointCreateResponse,
    SpmEndpointListResponse,
    SpmEndpointRead,
    SpmEndpointSyncRequest,
    SpmEndpointSyncResponse,
    SpmFindingDecisionCreate,
    SpmFindingDecisionRead,
    SpmFindingListResponse,
    SpmFindingRead,
)
from tracecat_ee.spm.service import SpmService, SpmSyncService

router = APIRouter(prefix="/spm", tags=["spm"])


def _pagination_params(
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
) -> CursorPaginationParams:
    return CursorPaginationParams.model_construct(limit=limit, cursor=cursor)


async def _require_spm_entitlement(
    role: OrgUserRole,
    session: AsyncDBSession,
) -> None:
    await check_entitlement(session, role, Entitlement.SPM)


def _parse_bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
        )
    return token


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
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


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
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get(
    "/assets",
    response_model=SpmAssetListResponse,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def list_spm_assets(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    pagination: CursorPaginationParams = Depends(_pagination_params),
) -> SpmAssetListResponse:
    service = SpmService(session, role=role)
    return await service.list_assets(pagination)


@router.get(
    "/assets/{asset_id}",
    response_model=SpmAssetRead,
    dependencies=[Depends(_require_spm_entitlement)],
)
@require_scope("org:read")
async def get_spm_asset(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    asset_id: uuid.UUID,
) -> SpmAssetRead:
    service = SpmService(session, role=role)
    try:
        return await service.get_asset(asset_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


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
    pagination: CursorPaginationParams = Depends(_pagination_params),
) -> SpmFindingListResponse:
    service = SpmService(session, role=role)
    return await service.list_findings(pagination)


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
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


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
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


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
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except EntitlementRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
