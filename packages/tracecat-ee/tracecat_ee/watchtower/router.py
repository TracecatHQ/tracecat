"""EE Watchtower monitor API router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from tracecat import config
from tracecat.auth.dependencies import OrgUserRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat_ee.watchtower.schemas import (
    WatchtowerAgentListResponse,
    WatchtowerAgentSessionListResponse,
    WatchtowerAgentToolCallListResponse,
    WatchtowerDisableAgentRequest,
    WatchtowerRevokeAgentSessionRequest,
)
from tracecat_ee.watchtower.service import WatchtowerService
from tracecat_ee.watchtower.types import WatchtowerAgentType

router = APIRouter(
    prefix="/watchtower/monitor",
    tags=["watchtower"],
)


async def _require_watchtower_entitlement(
    role: OrgUserRole,
    session: AsyncDBSession,
) -> None:
    await check_entitlement(session, role, Entitlement.WATCHTOWER)


@router.get(
    "/agents",
    response_model=WatchtowerAgentListResponse,
    dependencies=[Depends(_require_watchtower_entitlement)],
)
@require_scope("org:update")
async def list_watchtower_agents(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    agent_type: WatchtowerAgentType | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
) -> WatchtowerAgentListResponse:
    service = WatchtowerService(session, role=role)
    try:
        return await service.list_agents(
            limit=limit,
            cursor=cursor,
            agent_type=agent_type,
            status=status_filter,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "/agents/{agent_id}/sessions",
    response_model=WatchtowerAgentSessionListResponse,
    dependencies=[Depends(_require_watchtower_entitlement)],
)
@require_scope("org:update")
async def list_watchtower_agent_sessions(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    agent_id: uuid.UUID,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    workspace_id: uuid.UUID | None = Query(default=None),
    session_state: str | None = Query(default=None, alias="state"),
) -> WatchtowerAgentSessionListResponse:
    service = WatchtowerService(session, role=role)
    try:
        return await service.list_agent_sessions(
            agent_id=agent_id,
            limit=limit,
            cursor=cursor,
            workspace_id=workspace_id,
            state=session_state,
        )
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "/sessions/{session_id}/tool-calls",
    response_model=WatchtowerAgentToolCallListResponse,
    dependencies=[Depends(_require_watchtower_entitlement)],
)
@require_scope("org:update")
async def list_watchtower_session_tool_calls(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    session_id: uuid.UUID,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
) -> WatchtowerAgentToolCallListResponse:
    service = WatchtowerService(session, role=role)
    try:
        return await service.list_session_tool_calls(
            session_id=session_id,
            limit=limit,
            cursor=cursor,
            status=status_filter,
        )
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/sessions/{session_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_watchtower_entitlement)],
)
@require_scope("org:update")
async def revoke_watchtower_session(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    session_id: uuid.UUID,
    payload: WatchtowerRevokeAgentSessionRequest,
) -> None:
    service = WatchtowerService(session, role=role)
    try:
        await service.revoke_session(session_id, payload.reason)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/agents/{agent_id}/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_watchtower_entitlement)],
)
@require_scope("org:update")
async def disable_watchtower_agent(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    agent_id: uuid.UUID,
    payload: WatchtowerDisableAgentRequest,
) -> None:
    service = WatchtowerService(session, role=role)
    try:
        await service.disable_agent(agent_id, payload.reason)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/agents/{agent_id}/enable",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_watchtower_entitlement)],
)
@require_scope("org:update")
async def enable_watchtower_agent(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    agent_id: uuid.UUID,
) -> None:
    service = WatchtowerService(session, role=role)
    try:
        await service.enable_agent(agent_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
