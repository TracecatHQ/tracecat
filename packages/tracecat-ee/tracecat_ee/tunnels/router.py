"""Workspace network tunnel API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession, AsyncDBSessionBypass
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import Page, PageParams, PaginationError
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat_ee.tunnels.gateways import (
    TunnelGatewayUnavailableError,
    kubernetes_enabled,
)
from tracecat_ee.tunnels.pangolin import PangolinError
from tracecat_ee.tunnels.schemas import (
    ManagedTunnelCreate,
    ManagedTunnelCreated,
    TunnelAvailabilityRead,
    TunnelCreate,
    TunnelDetailRead,
    TunnelGatewayKeyRead,
    TunnelGatewayPolicyRead,
    TunnelRead,
    TunnelStatusRead,
    TunnelUpdate,
)
from tracecat_ee.tunnels.service import (
    TunnelDisabledError,
    TunnelInUseError,
    TunnelsService,
    get_gateway_policy,
)
from tracecat_ee.tunnels.tokens import TunnelTokenError


def _tunnel_in_use(exc: TunnelInUseError, service: TunnelsService) -> HTTPException:
    referenced_by = service.readable_references(exc.referenced_by)
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "tunnel_in_use",
            "message": str(exc),
            "referenced_by": [ref.model_dump(mode="json") for ref in referenced_by],
        },
    )


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


@router.get("/availability")
@require_scope("tunnel:read")
async def get_tunnel_availability(
    *,
    role: WorkspaceActorRouteRole,
) -> TunnelAvailabilityRead:
    """Report whether this deployment can run tunnel gateways."""
    return TunnelAvailabilityRead(available=kubernetes_enabled())


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
    except TunnelGatewayUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
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
    except TunnelGatewayUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
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
) -> TunnelDetailRead:
    """Get a tunnel in the workspace and the resources that reference it."""
    service = TunnelsService(session, role=role)
    try:
        tunnel = await service.get_tunnel(tunnel_id)
        referenced_by = service.readable_references(
            await service.list_tunnel_references(tunnel)
        )
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return TunnelDetailRead(
        **TunnelRead.from_database(tunnel).model_dump(), referenced_by=referenced_by
    )


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
    except TunnelInUseError as exc:
        raise _tunnel_in_use(exc, service) from exc
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
    except TunnelInUseError as exc:
        raise _tunnel_in_use(exc, service) from exc
    except IntegrityError as exc:
        # An MCP integration started using the tunnel after the reference check.
        raise _tunnel_in_use(
            TunnelInUseError("Tunnel is in use. Reload and try again.", []), service
        ) from exc
    except PangolinError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to delete the tunnel's Pangolin objects; retry the delete",
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
    except TunnelGatewayUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return TunnelGatewayKeyRead(tunnel_id=tunnel.id, gateway_key=gateway_key)


gateway_router = APIRouter(
    prefix="/tunnel-gateway", tags=["tunnels"], include_in_schema=False
)
_gateway_bearer = HTTPBearer(auto_error=False)


@gateway_router.post("/policy")
async def get_tunnel_gateway_policy(
    *,
    session: AsyncDBSessionBypass,
    credentials: HTTPAuthorizationCredentials | None = Depends(_gateway_bearer),
) -> TunnelGatewayPolicyRead:
    """Return the tunnel policy for the gateway that holds the tunnel's gateway key.

    Gateways poll this so disabling a tunnel, rotating its key, or deleting it
    revokes a running gateway.
    """
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        return await get_gateway_policy(session, credentials.credentials)
    except TunnelTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc
    except TunnelDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN) from exc
