"""Route MCP integration traffic through a workspace tunnel."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import WorkspaceTunnel
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.integrations.mcp_validation import MCPConfigurationError
from tracecat.outbound import OutboundHTTPTransport, create_outbound_http_client
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement

_oauth_tunnel_proxy: ContextVar[str | None] = ContextVar(
    "mcp_oauth_tunnel_proxy", default=None
)
_oauth_route_pinned: ContextVar[bool] = ContextVar(
    "mcp_oauth_route_pinned", default=False
)


def mcp_server_target(server_uri: str) -> tuple[str, int]:
    """Return the host and port a request to the MCP server connects to."""
    parsed = urlparse(server_uri)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("Server URI must include a hostname")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    return host, port


async def validate_mcp_tunnel(
    session: AsyncSession,
    *,
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    tunnel_id: uuid.UUID,
    server_uri: str,
) -> None:
    """Check an MCP server can be reached through the tunnel.

    Raises:
        ValueError: If the tunnel is unavailable or does not allow the server.
    """
    if not await is_org_entitled(session, organization_id, Entitlement.TUNNELS):
        raise ValueError("Tunnels are not available on your current plan")
    tunnel = (
        await session.execute(
            select(WorkspaceTunnel).where(
                WorkspaceTunnel.workspace_id == workspace_id,
                WorkspaceTunnel.id == tunnel_id,
            )
        )
    ).scalar_one_or_none()
    if tunnel is None:
        raise ValueError("Tunnel not found in this workspace")
    if not tunnel.enabled:
        raise ValueError(f"Tunnel {tunnel.name!r} is disabled")
    host, port = mcp_server_target(server_uri)
    allowed = {
        (str(target["host"]).lower(), int(allowed_port))
        for target in tunnel.allowed_targets
        for allowed_port in target.get("ports", [])
    }
    if (host, port) not in allowed:
        raise ValueError(
            f"Tunnel {tunnel.name!r} does not allow {host}:{port}. "
            "Add it to the tunnel's allowed targets first."
        )


async def resolve_mcp_tunnel_proxy(
    *,
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    tunnel_id: uuid.UUID,
    subject: str,
) -> str:
    """Mint a gateway proxy URL for MCP traffic. Never falls back to direct.

    Raises:
        MCPConfigurationError: If the tunnel cannot be used right now.
    """
    from tracecat_ee.tunnels.routing import TunnelRouteError, resolve_tunnel_route

    try:
        route = await resolve_tunnel_route(
            workspace_id=workspace_id,
            organization_id=organization_id,
            tunnel_id=tunnel_id,
            subject=subject,
        )
    except TunnelRouteError as e:
        raise MCPConfigurationError(f"MCP server tunnel cannot be used: {e}") from e
    return route.proxy_url


@asynccontextmanager
async def mcp_oauth_tunnel(
    *,
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    tunnel_id: uuid.UUID | None,
    subject: str,
) -> AsyncIterator[None]:
    """Send MCP OAuth discovery, registration and token calls through a tunnel.

    With no tunnel, the calls are pinned to go direct.
    """
    proxy = (
        await resolve_mcp_tunnel_proxy(
            workspace_id=workspace_id,
            organization_id=organization_id,
            tunnel_id=tunnel_id,
            subject=subject,
        )
        if tunnel_id is not None
        else None
    )
    with mcp_oauth_route(proxy):
        yield


@contextmanager
def mcp_oauth_route(proxy: str | None) -> Iterator[None]:
    """Pin the route of MCP OAuth calls in this context: a tunnel proxy, or direct.

    Token refreshes inside it use this route instead of looking up the stored
    MCP integration, so verifying an unsaved tunnel change refreshes through
    the new route.
    """
    proxy_token = _oauth_tunnel_proxy.set(proxy)
    pinned_token = _oauth_route_pinned.set(True)
    try:
        yield
    finally:
        _oauth_route_pinned.reset(pinned_token)
        _oauth_tunnel_proxy.reset(proxy_token)


def mcp_oauth_route_pinned() -> bool:
    """Whether an MCP OAuth route is pinned for this context."""
    return _oauth_route_pinned.get()


def mcp_oauth_tunnel_proxy() -> str | None:
    """The tunnel proxy for the MCP OAuth flow in progress, if any."""
    return _oauth_tunnel_proxy.get()


def create_mcp_oauth_http_client() -> httpx.AsyncClient:
    """HTTP client for MCP OAuth metadata and registration requests."""
    if proxy := mcp_oauth_tunnel_proxy():
        return create_outbound_http_client(tunnel_proxy=proxy)
    return httpx.AsyncClient()


def mcp_oauth_client_transport() -> dict[str, object]:
    """Extra authlib client options that route token calls through the tunnel."""
    if proxy := mcp_oauth_tunnel_proxy():
        return {
            "transport": OutboundHTTPTransport(tunnel_proxy=proxy),
            "trust_env": False,
        }
    return {}
