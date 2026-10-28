"""Keep tunnel gateways in step with tunnels for the configured backend."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.locks import try_pg_advisory_xact_lock
from tracecat.db.models import Workspace, WorkspaceTunnel
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.secrets.encryption import decrypt_value
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement
from tracecat_ee.tunnels.k8s import (
    GatewaySpec,
    KubernetesClient,
    KubernetesError,
    KubernetesGateways,
    TunnelGatewayBackend,
    gateway_backend,
    reconcile_forever,
)

# Arbitrary constant that identifies the gateway reconcile leader lock.
_RECONCILE_LOCK_KEY = 0x7472_6163_7475_6E6C


TUNNELS_REQUIRE_KUBERNETES = (
    "Tunnels require Tracecat on Kubernetes with tunnel gateways enabled "
    "(TRACECAT__TUNNEL_GATEWAY_BACKEND=kubernetes)"
)


class TunnelGatewayUnavailableError(TracecatValidationError):
    """No gateway backend is configured, so tunnels cannot be used."""


def kubernetes_enabled() -> bool:
    return gateway_backend() is TunnelGatewayBackend.KUBERNETES


def require_kubernetes_gateways() -> None:
    if not kubernetes_enabled():
        raise TunnelGatewayUnavailableError(TUNNELS_REQUIRE_KUBERNETES)


def _decrypt(value: bytes) -> str:
    return decrypt_value(value, key=get_db_encryption_key()).decode("utf-8")


def gateway_spec(
    tunnel: WorkspaceTunnel, *, entitled: bool = True
) -> GatewaySpec | None:
    """Return the gateway spec for a tunnel, or ``None`` if it has no gateway key."""
    if tunnel.encrypted_gateway_key is None:
        return None
    return GatewaySpec(
        tunnel_id=tunnel.id,
        endpoint=tunnel.endpoint,
        client_id=tunnel.client_id,
        client_secret=_decrypt(tunnel.encrypted_client_secret),
        gateway_key=_decrypt(tunnel.encrypted_gateway_key),
        enabled=tunnel.enabled and entitled,
    )


async def sync_gateway(tunnel: WorkspaceTunnel) -> None:
    """Apply a tunnel's gateway objects. Failures are left to the reconciler."""
    if not kubernetes_enabled():
        return
    spec = gateway_spec(tunnel)
    try:
        async with KubernetesClient() as client:
            gateways = KubernetesGateways(client)
            if spec is None:
                await gateways.delete(tunnel.id)
            else:
                await gateways.apply(spec)
    except (KubernetesError, OSError) as exc:
        logger.warning(
            "Failed to sync tunnel gateway; the reconciler will retry",
            tunnel_id=str(tunnel.id),
            error=str(exc),
        )


async def delete_gateway(tunnel_id: uuid.UUID) -> None:
    """Delete a tunnel's gateway objects. Failures are left to the reconciler."""
    if not kubernetes_enabled():
        return
    try:
        async with KubernetesClient() as client:
            await KubernetesGateways(client).delete(tunnel_id)
    except (KubernetesError, OSError) as exc:
        logger.warning(
            "Failed to delete tunnel gateway; the reconciler will retry",
            tunnel_id=str(tunnel_id),
            error=str(exc),
        )


async def gateway_ready(tunnel_id: uuid.UUID) -> bool | None:
    """Whether the tunnel's gateway is ready; ``None`` if Tracecat does not run it."""
    if not kubernetes_enabled():
        return None
    try:
        async with KubernetesClient() as client:
            return await KubernetesGateways(client).ready(tunnel_id)
    except (KubernetesError, OSError) as exc:
        logger.warning(
            "Failed to read tunnel gateway status",
            tunnel_id=str(tunnel_id),
            error=str(exc),
        )
        return None


async def desired_gateway_specs(session: AsyncSession) -> list[GatewaySpec]:
    """Every tunnel with a gateway key, scaled to zero when disabled or unentitled."""
    result = await session.execute(
        select(WorkspaceTunnel, Workspace.organization_id).join(
            Workspace, Workspace.id == WorkspaceTunnel.workspace_id
        )
    )
    entitled: dict[OrganizationID, bool] = {}
    specs: list[GatewaySpec] = []
    for tunnel, organization_id in result.tuples().all():
        if organization_id not in entitled:
            entitled[organization_id] = await is_org_entitled(
                session, organization_id, Entitlement.TUNNELS
            )
        if spec := gateway_spec(tunnel, entitled=entitled[organization_id]):
            specs.append(spec)
    return specs


async def reconcile_gateways_once() -> None:
    """Reconcile gateway objects if this process holds the leader lock."""
    async with get_async_session_bypass_rls_context_manager() as session:
        # The session never commits, so the lock is held until it closes.
        if not await try_pg_advisory_xact_lock(session, _RECONCILE_LOCK_KEY):
            return
        specs = await desired_gateway_specs(session)
        async with KubernetesClient() as client:
            applied, deleted = await KubernetesGateways(client).reconcile(specs)
        if deleted:
            logger.info("Reconciled tunnel gateways", applied=applied, deleted=deleted)


async def start_tunnel_gateway_reconciler(
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run the gateway reconcile loop until stopped."""
    await reconcile_forever(
        reconcile_gateways_once,
        interval=config.TRACECAT__TUNNEL_GATEWAY_RECONCILE_INTERVAL,
        stop_event=stop_event,
    )
