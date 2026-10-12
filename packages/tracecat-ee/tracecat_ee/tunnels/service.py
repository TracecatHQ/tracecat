"""Workspace network tunnel service."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from tracecat.audit.logger import audit_log
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.authz.controls import require_scope
from tracecat.db.models import WorkspaceTunnel
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import Page, PageParams, paginate
from tracecat.secrets.encryption import decrypt_value, encrypt_value
from tracecat.service import BaseWorkspaceService
from tracecat.tiers.enums import Entitlement
from tracecat_ee.tunnels.schemas import (
    TunnelCreate,
    TunnelUpdate,
    serialize_endpoint,
    serialize_targets,
)
from tracecat_ee.tunnels.tokens import generate_gateway_key


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

    def _ensure_owned(self, tunnel: WorkspaceTunnel) -> None:
        if tunnel.workspace_id != self.workspace_id:
            raise TracecatNotFoundError("Tunnel not found")

    @staticmethod
    def _encrypt_secret(value: str) -> bytes:
        return encrypt_value(value.encode("utf-8"), key=get_db_encryption_key())
