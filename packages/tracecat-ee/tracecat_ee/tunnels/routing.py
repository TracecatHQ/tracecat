"""Route one action's HTTP(S) traffic through its tunnel's gateway."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import uuid
from dataclasses import dataclass, field
from urllib.parse import quote

from sqlalchemy import select

from tracecat import config
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import WorkspaceTunnel
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import RunActionInput
from tracecat.exceptions import TracecatException
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.secrets.encryption import decrypt_value
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement
from tracecat_ee.tunnels.tokens import TUNNEL_TOKEN_MAX_TTL_SECONDS, mint_tunnel_token

PROXY_USERNAME = "tracecat"


class TunnelRouteError(TracecatException):
    """The action's tunnel cannot be used. The action must not run."""


def effective_tunnel(input: RunActionInput) -> str | None:
    """The action's tunnel, falling back to the workflow default.

    The workflow default skips platform actions such as subflows, loops,
    agents, and Python scripts; they cannot be routed through a tunnel.
    """
    if input.task.tunnel:
        return input.task.tunnel
    if PlatformAction.is_interface(input.task.action):
        return None
    return input.run_context.tunnel


@dataclass(frozen=True)
class TunnelRoute:
    """Where a tunneled action sends traffic, and how it authenticates."""

    tunnel_id: uuid.UUID
    tunnel_name: str
    gateway_ip: str
    gateway_port: int
    proxy_url: str = field(repr=False)


def gateway_host(tunnel_id: uuid.UUID) -> str:
    return config.TRACECAT__TUNNEL_GATEWAY_HOST_TEMPLATE.format(tunnel_id=tunnel_id)


async def _resolve_gateway_ip(host: str, port: int) -> str:
    """Resolve the gateway once so the sandbox can be locked to one address."""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            host, port, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise TunnelRouteError(f"Tunnel gateway {host} is unreachable") from exc
    if not infos:
        raise TunnelRouteError(f"Tunnel gateway {host} is unreachable")
    return str(infos[0][4][0])


async def resolve_tunnel_route(
    *,
    name: str,
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    wf_exec_id: str,
    action_ref: str | None,
    ttl_seconds: int = TUNNEL_TOKEN_MAX_TTL_SECONDS,
) -> TunnelRoute:
    """Check the tunnel can be used now and mint a proxy token for it.

    The lookup is filtered by workspace explicitly; the executor has no
    workspace role for RLS. Errors never fall back to direct egress.
    """
    async with get_async_session_bypass_rls_context_manager() as session:
        if not await is_org_entitled(session, organization_id, Entitlement.TUNNELS):
            raise TunnelRouteError("Tunnels are not available on your current plan")
        result = await session.execute(
            select(WorkspaceTunnel).where(
                WorkspaceTunnel.workspace_id == workspace_id,
                WorkspaceTunnel.name == name,
            )
        )
        tunnel = result.scalar_one_or_none()
        if tunnel is None:
            raise TunnelRouteError(f"Tunnel {name!r} not found in this workspace")
        if not tunnel.enabled:
            raise TunnelRouteError(f"Tunnel {name!r} is disabled")
        if tunnel.encrypted_gateway_key is None:
            raise TunnelRouteError(f"Tunnel {name!r} has no gateway key")
        tunnel_id = tunnel.id
        gateway_key = decrypt_value(
            tunnel.encrypted_gateway_key, key=get_db_encryption_key()
        ).decode("utf-8")

    token = mint_tunnel_token(
        gateway_key=gateway_key,
        tunnel_id=tunnel_id,
        workspace_id=workspace_id,
        wf_exec_id=wf_exec_id,
        action_ref=action_ref,
        ttl_seconds=ttl_seconds,
    )
    port = config.TRACECAT__TUNNEL_GATEWAY_PORT
    ip = await _resolve_gateway_ip(gateway_host(tunnel_id), port)
    return TunnelRoute(
        tunnel_id=tunnel_id,
        tunnel_name=name,
        gateway_ip=ip,
        gateway_port=port,
        proxy_url=f"http://{PROXY_USERNAME}:{quote(token, safe='')}@{ip}:{port}",
    )


def tunnel_proxy_env(proxy_url: str) -> dict[str, str]:
    """Environment that sends every HTTP(S) client in the action through the gateway."""
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "ALL_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "all_proxy": proxy_url,
        "NO_PROXY": "",
        "no_proxy": "",
        "TRACECAT__TUNNEL_PROXY_URL": proxy_url,
    }
