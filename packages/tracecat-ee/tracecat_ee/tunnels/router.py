"""Workspace network tunnel API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import Page, PageParams, PaginationError
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat_ee.tunnels.pangolin import PangolinError
from tracecat_ee.tunnels.schemas import (
    ManagedTunnelCreate,
    ManagedTunnelCreated,
    TunnelCreate,
    TunnelGatewayKeyRead,
    TunnelRead,
    TunnelStatusRead,
    TunnelUpdate,
)
from tracecat_ee.tunnels.service import TunnelsService


async def _require_entitlement(
    role: WorkspaceActorRouteRole, session: AsyncDBSession
) -> None:
    await check_entitlement(session, role, Entitlement.TUNNELS)


router = APIRouter(
    prefix="/tunnels",
    tags=["tunnels"],
    dependencies=[Depends(_require_entitlement)],
)

_TUNNEL_EXISTS = "A tunnel with this name already exists in the workspace"


@router.get("")
@require_scope("tunnel:read")
async def list_tunnels(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    limit: int = Query(
        config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(None, max_length=8192),
) -> Page[TunnelRead]:
    """List tunnels in the workspace."""
    service = TunnelsService(session, role=role)
    try:
        tunnels = await service.list_tunnels(PageParams(limit=limit, cursor=cursor))
    except PaginationError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return Page(
        items=[TunnelRead.from_database(tunnel) for tunnel in tunnels.items],
        next_cursor=tunnels.next_cursor,
        prev_cursor=tunnels.prev_cursor,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("tunnel:create")
async def create_tunnel(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: TunnelCreate,
) -> TunnelRead:
    """Create a tunnel in the workspace."""
    service = TunnelsService(session, role=role)
    try:
        tunnel = await service.create_tunnel(params)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_TUNNEL_EXISTS
        ) from exc
    return TunnelRead.from_database(tunnel)


@router.post("/managed", status_code=status.HTTP_201_CREATED)
@require_scope("tunnel:create")
async def create_managed_tunnel(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: ManagedTunnelCreate,
) -> ManagedTunnelCreated:
    """Create a tunnel whose Pangolin site, machine client, and resources Tracecat provisions.

    The site credentials and install command are returned only in this response.
    """
    service = TunnelsService(session, role=role)
    try:
        return await service.create_managed_tunnel(params)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_TUNNEL_EXISTS
        ) from exc
    except PangolinError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to provision the tunnel in Pangolin",
        ) from exc


@router.get("/{tunnel_id}/status")
@require_scope("tunnel:read")
async def get_tunnel_status(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    tunnel_id: uuid.UUID,
) -> TunnelStatusRead:
    """Get whether a managed tunnel's on-prem site is connected."""
    service = TunnelsService(session, role=role)
    try:
        tunnel = await service.get_tunnel(tunnel_id)
        return await service.get_tunnel_status(tunnel)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except PangolinError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to read the tunnel status from Pangolin",
        ) from exc


@router.get("/{tunnel_id}")
@require_scope("tunnel:read")
async def get_tunnel(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    tunnel_id: uuid.UUID,
) -> TunnelRead:
    """Get a tunnel in the workspace."""
    service = TunnelsService(session, role=role)
    try:
        tunnel = await service.get_tunnel(tunnel_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return TunnelRead.from_database(tunnel)


@router.patch("/{tunnel_id}")
@require_scope("tunnel:update")
async def update_tunnel(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    tunnel_id: uuid.UUID,
    params: TunnelUpdate,
) -> TunnelRead:
    """Update a tunnel. Sending `client_secret` rotates the stored secret."""
    service = TunnelsService(session, role=role)
    try:
        tunnel = await service.get_tunnel(tunnel_id)
        updated = await service.update_tunnel(tunnel, params)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_TUNNEL_EXISTS
        ) from exc
    return TunnelRead.from_database(updated)


@router.delete("/{tunnel_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("tunnel:delete")
async def delete_tunnel(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    tunnel_id: uuid.UUID,
) -> None:
    """Delete a tunnel from the workspace."""
    service = TunnelsService(session, role=role)
    try:
        tunnel = await service.get_tunnel(tunnel_id)
        await service.delete_tunnel(tunnel)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.post("/{tunnel_id}/gateway-key", status_code=status.HTTP_201_CREATED)
@require_scope("tunnel:update")
async def issue_tunnel_gateway_key(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    tunnel_id: uuid.UUID,
) -> TunnelGatewayKeyRead:
    """Issue a gateway key for the tunnel, revoking the previous key.

    The key is returned only in this response.
    """
    service = TunnelsService(session, role=role)
    try:
        tunnel = await service.get_tunnel(tunnel_id)
        gateway_key = await service.issue_gateway_key(tunnel)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return TunnelGatewayKeyRead(tunnel_id=tunnel.id, gateway_key=gateway_key)
