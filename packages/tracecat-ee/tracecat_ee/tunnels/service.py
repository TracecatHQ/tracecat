"""Workspace network tunnel service."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.logger import audit_log
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.authz.controls import require_scope
from tracecat.db.models import Workspace, WorkspaceTunnel
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import Page, PageParams, paginate
from tracecat.secrets.encryption import decrypt_value, encrypt_value
from tracecat.service import BaseWorkspaceService
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement
from tracecat_ee.tunnels.pangolin import (
    PangolinAPI,
    PangolinError,
    PangolinSettings,
    managed_alias,
)
from tracecat_ee.tunnels.schemas import (
    ManagedTunnelCreate,
    ManagedTunnelCreated,
    TunnelCreate,
    TunnelGatewayPolicyRead,
    TunnelRead,
    TunnelStatusRead,
    TunnelUpdate,
    serialize_endpoint,
    serialize_targets,
)
from tracecat_ee.tunnels.tokens import (
    TunnelTokenError,
    generate_gateway_key,
    unverified_policy_token_tunnel_id,
    verify_gateway_policy_token,
)


class TunnelDisabledError(Exception):
    """The tunnel is disabled or its organization lost the tunnels entitlement."""


async def get_gateway_policy(
    session: AsyncSession, token: str
) -> TunnelGatewayPolicyRead:
    """Return a tunnel's current policy to the gateway holding its gateway key.

    The gateway authenticates with a token signed by the tunnel's current
    gateway key, so a rotated key, a deleted tunnel, or a token for another
    tunnel all fail with ``TunnelTokenError``. The session must bypass RLS
    because gateways have no workspace role; the key check is the guard.
    """
    tunnel_id = unverified_policy_token_tunnel_id(token)
    result = await session.execute(
        select(WorkspaceTunnel, Workspace.organization_id)
        .join(Workspace, Workspace.id == WorkspaceTunnel.workspace_id)
        .where(WorkspaceTunnel.id == tunnel_id)
    )
    row = result.one_or_none()
    if row is None or row[0].encrypted_gateway_key is None:
        raise TunnelTokenError("Invalid gateway policy token")
    tunnel, organization_id = row
    gateway_key = decrypt_value(
        tunnel.encrypted_gateway_key, key=get_db_encryption_key()
    ).decode("utf-8")
    verify_gateway_policy_token(token, gateway_key=gateway_key, tunnel_id=tunnel.id)
    if not tunnel.enabled or not await is_org_entitled(
        session, organization_id, Entitlement.TUNNELS
    ):
        raise TunnelDisabledError("Tunnel is disabled")
    return TunnelGatewayPolicyRead(
        tunnel_id=tunnel.id,
        workspace_id=tunnel.workspace_id,
        allowed_targets=TunnelRead.from_database(tunnel).allowed_targets,
        managed=tunnel.managed,
    )


class TunnelsService(BaseWorkspaceService):
    """Manage network tunnels owned by the current workspace.

    Every lookup is filtered by the service role's workspace, so a tunnel ID or
    name from another workspace resolves to not found.
    """

    service_name = "tunnels"

    @require_scope("tunnel:read")
    async def list_tunnels(self, page: PageParams) -> Page[WorkspaceTunnel]:
        await self.require_entitlement(Entitlement.TUNNELS)
        stmt = select(WorkspaceTunnel).where(
            WorkspaceTunnel.workspace_id == self.workspace_id
        )
        return await paginate(
            self.session,
            stmt,
            page=page,
            order_by=(WorkspaceTunnel.created_at.asc(), WorkspaceTunnel.id.asc()),
        )

    @require_scope("tunnel:read")
    async def get_tunnel(self, tunnel_id: uuid.UUID) -> WorkspaceTunnel:
        await self.require_entitlement(Entitlement.TUNNELS)
        stmt = select(WorkspaceTunnel).where(
            WorkspaceTunnel.workspace_id == self.workspace_id,
            WorkspaceTunnel.id == tunnel_id,
        )
        result = await self.session.execute(stmt)
        if (tunnel := result.scalar_one_or_none()) is None:
            raise TracecatNotFoundError("Tunnel not found")
        return tunnel

    @require_scope("tunnel:read")
    async def get_tunnel_by_name(self, name: str) -> WorkspaceTunnel:
        await self.require_entitlement(Entitlement.TUNNELS)
        stmt = select(WorkspaceTunnel).where(
            WorkspaceTunnel.workspace_id == self.workspace_id,
            WorkspaceTunnel.name == name,
        )
        result = await self.session.execute(stmt)
        if (tunnel := result.scalar_one_or_none()) is None:
            raise TracecatNotFoundError(f"Tunnel {name!r} not found")
        return tunnel

    @require_scope("tunnel:create")
    @audit_log(resource_type="workspace_tunnel", action="create")
    async def create_tunnel(self, params: TunnelCreate) -> WorkspaceTunnel:
        await self.require_entitlement(Entitlement.TUNNELS)
        tunnel = WorkspaceTunnel(
            workspace_id=self.workspace_id,
            name=params.name,
            description=params.description,
            provider=params.provider.value,
            endpoint=serialize_endpoint(params.endpoint),
            client_id=params.client_id,
            encrypted_client_secret=self._encrypt_secret(
                params.client_secret.get_secret_value()
            ),
            allowed_targets=serialize_targets(params.allowed_targets),
            enabled=params.enabled,
        )
        self.session.add(tunnel)
        await self.session.commit()
        await self.session.refresh(tunnel)
        return tunnel

    @require_scope("tunnel:update")
    @audit_log(resource_type="workspace_tunnel", action="update")
    async def update_tunnel(
        self, tunnel: WorkspaceTunnel, params: TunnelUpdate
    ) -> WorkspaceTunnel:
        await self.require_entitlement(Entitlement.TUNNELS)
        self._ensure_owned(tunnel)
        fields = params.model_fields_set
        managed_fields = {"endpoint", "client_id", "client_secret", "allowed_targets"}
        if tunnel.managed and fields & managed_fields:
            raise TracecatValidationError(
                "Endpoint, credentials, and targets of a managed tunnel cannot be changed"
            )
        if "name" in fields and params.name is not None:
            tunnel.name = params.name
        if "description" in fields:
            tunnel.description = params.description
        if "endpoint" in fields and params.endpoint is not None:
            tunnel.endpoint = serialize_endpoint(params.endpoint)
        if "client_id" in fields and params.client_id is not None:
            tunnel.client_id = params.client_id
        if "client_secret" in fields and params.client_secret is not None:
            tunnel.encrypted_client_secret = self._encrypt_secret(
                params.client_secret.get_secret_value()
            )
        if "allowed_targets" in fields and params.allowed_targets is not None:
            tunnel.allowed_targets = serialize_targets(params.allowed_targets)
        if "enabled" in fields and params.enabled is not None:
            tunnel.enabled = params.enabled
        self.session.add(tunnel)
        await self.session.commit()
        await self.session.refresh(tunnel)
        return tunnel

    @require_scope("tunnel:delete")
    @audit_log(resource_type="workspace_tunnel", action="delete")
    async def delete_tunnel(self, tunnel: WorkspaceTunnel) -> None:
        await self.require_entitlement(Entitlement.TUNNELS)
        self._ensure_owned(tunnel)
        if tunnel.managed:
            await self._delete_managed_resources(tunnel)
        await self.session.delete(tunnel)
        await self.session.commit()

    @require_scope("tunnel:update")
    @audit_log(resource_type="workspace_tunnel", action="update")
    async def issue_gateway_key(self, tunnel: WorkspaceTunnel) -> str:
        """Issue a new gateway key, revoking any previous one. Returns it once."""
        await self.require_entitlement(Entitlement.TUNNELS)
        self._ensure_owned(tunnel)
        gateway_key = generate_gateway_key()
        tunnel.encrypted_gateway_key = self._encrypt_secret(gateway_key)
        self.session.add(tunnel)
        await self.session.commit()
        await self.session.refresh(tunnel)
        return gateway_key

    @require_scope("tunnel:read")
    async def get_gateway_key(self, tunnel: WorkspaceTunnel) -> str:
        """Return the tunnel's gateway key for signing proxy tokens."""
        await self.require_entitlement(Entitlement.TUNNELS)
        self._ensure_owned(tunnel)
        if tunnel.encrypted_gateway_key is None:
            raise TracecatValidationError(
                f"Tunnel {tunnel.name!r} has no gateway key. Issue one first."
            )
        return decrypt_value(
            tunnel.encrypted_gateway_key, key=get_db_encryption_key()
        ).decode("utf-8")

    @require_scope("tunnel:create")
    @audit_log(resource_type="workspace_tunnel", action="create")
    async def create_managed_tunnel(
        self, params: ManagedTunnelCreate
    ) -> ManagedTunnelCreated:
        """Provision a Pangolin site, machine client, and resources for a new tunnel.

        Each target becomes a private host resource that only this tunnel's
        machine client can use. Anything created in Pangolin is rolled back if
        a later step fails.
        """
        await self.require_entitlement(Entitlement.TUNNELS)
        settings = self._pangolin_settings()
        tunnel_id = uuid.uuid4()
        label = f"tracecat-{self.workspace_id}-{params.name}"
        created_resources: list[int] = []
        site_id: int | None = None
        client_id: int | None = None
        async with PangolinAPI(settings) as api:
            try:
                site = await api.create_site(label)
                site_id = site.site_id
                client = await api.create_machine_client(label)
                client_id = client.client_id
                resource_ids: dict[str, int] = {}
                for target in params.allowed_targets:
                    resource_id = await api.create_host_resource(
                        name=f"{label}-{target.host}",
                        site_id=site.site_id,
                        destination=target.host,
                        tcp_ports=target.ports,
                        client_id=client.client_id,
                        alias=managed_alias(tunnel_id, target.host),
                    )
                    created_resources.append(resource_id)
                    resource_ids[target.host] = resource_id
                tunnel = WorkspaceTunnel(
                    id=tunnel_id,
                    workspace_id=self.workspace_id,
                    name=params.name,
                    description=params.description,
                    provider="pangolin",
                    endpoint=settings.endpoint,
                    client_id=client.credentials.id,
                    encrypted_client_secret=self._encrypt_secret(
                        client.credentials.secret
                    ),
                    allowed_targets=serialize_targets(params.allowed_targets),
                    enabled=True,
                    managed=True,
                    pangolin_site_id=site.site_id,
                    pangolin_client_id=client.client_id,
                    pangolin_resource_ids=resource_ids,
                )
                self.session.add(tunnel)
                await self.session.commit()
            except BaseException:
                await self.session.rollback()
                await self._cleanup_pangolin(api, created_resources, client_id, site_id)
                raise
        await self.session.refresh(tunnel)
        command = (
            f"newt --id {site.credentials.id} --secret {site.credentials.secret}"
            f" --endpoint {settings.endpoint}"
        )
        return ManagedTunnelCreated(
            tunnel=TunnelRead.from_database(tunnel),
            site_id=site.credentials.id,
            site_secret=site.credentials.secret,
            site_endpoint=settings.endpoint,
            site_install_command=command,
        )

    @require_scope("tunnel:read")
    async def get_tunnel_status(self, tunnel: WorkspaceTunnel) -> TunnelStatusRead:
        await self.require_entitlement(Entitlement.TUNNELS)
        self._ensure_owned(tunnel)
        site_online: bool | None = None
        if tunnel.managed and tunnel.pangolin_site_id is not None:
            async with PangolinAPI(self._pangolin_settings()) as api:
                site_online = await api.site_online(tunnel.pangolin_site_id)
        return TunnelStatusRead(
            tunnel_id=tunnel.id, managed=tunnel.managed, site_online=site_online
        )

    @require_scope("tunnel:read")
    async def find_isolation_violations(self, tunnel: WorkspaceTunnel) -> list[str]:
        """Return resources of a managed tunnel granted to any client but its own."""
        await self.require_entitlement(Entitlement.TUNNELS)
        self._ensure_owned(tunnel)
        if not tunnel.managed or not tunnel.pangolin_resource_ids:
            return []
        violations: list[str] = []
        async with PangolinAPI(self._pangolin_settings()) as api:
            for host, resource_id in tunnel.pangolin_resource_ids.items():
                granted = await api.resource_client_ids(resource_id)
                if granted != {tunnel.pangolin_client_id}:
                    violations.append(host)
        return violations

    def _pangolin_settings(self) -> PangolinSettings:
        settings = PangolinSettings.from_config()
        if settings is None:
            raise TracecatValidationError(
                "Managed tunnels are not configured for this deployment"
            )
        return settings

    async def _delete_managed_resources(self, tunnel: WorkspaceTunnel) -> None:
        async with PangolinAPI(self._pangolin_settings()) as api:
            await self._cleanup_pangolin(
                api,
                list((tunnel.pangolin_resource_ids or {}).values()),
                tunnel.pangolin_client_id,
                tunnel.pangolin_site_id,
                raise_errors=True,
            )

    async def _cleanup_pangolin(
        self,
        api: PangolinAPI,
        resource_ids: list[int],
        client_id: int | None,
        site_id: int | None,
        *,
        raise_errors: bool = False,
    ) -> None:
        steps: list[tuple[Callable[[int], Awaitable[None]], int]] = [
            (api.delete_resource, r) for r in resource_ids
        ]
        if client_id is not None:
            steps.append((api.delete_client, client_id))
        if site_id is not None:
            steps.append((api.delete_site, site_id))
        for delete, object_id in steps:
            try:
                await delete(object_id)
            except PangolinError as exc:
                self.logger.warning(
                    "Failed to delete Pangolin object",
                    object_id=object_id,
                    error=str(exc),
                )
                if raise_errors:
                    raise

    def _ensure_owned(self, tunnel: WorkspaceTunnel) -> None:
        if tunnel.workspace_id != self.workspace_id:
            raise TracecatNotFoundError("Tunnel not found")

    @staticmethod
    def _encrypt_secret(value: str) -> bytes:
        return encrypt_value(value.encode("utf-8"), key=get_db_encryption_key())
