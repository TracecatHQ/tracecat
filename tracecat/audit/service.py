from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Self

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.enums import AuditEventActor, AuditEventStatus
from tracecat.audit.types import AuditAction, AuditEvent, AuditResourceType
from tracecat.auth.types import PlatformRole, Role
from tracecat.contexts import ctx_client_ip, ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import User
from tracecat.service import BaseService

# Union type for roles that can be used for audit logging
AuditableRole = Role | PlatformRole


class AuditService(BaseService):
    """Stream user-driven events to an audit webhook if configured.

    This service accepts an optional role to support both:
    - Platform operations (PlatformRole - no org/workspace context)
    - Org-scoped operations (Role - with org context)

    The role is used for audit attribution (who performed the action).
    """

    service_name = "audit"
    role: AuditableRole | None

    def __init__(self, session: AsyncSession, role: AuditableRole | None = None):
        super().__init__(session)
        self.role = role or ctx_role.get()
        # Don't require organization_id - platform ops won't have one

    @classmethod
    @asynccontextmanager
    async def with_session(
        cls,
        role: AuditableRole | None = None,
        *,
        session: AsyncSession | None = None,
    ) -> AsyncGenerator[Self, None]:
        """Create an AuditService instance with a database session.

        Override BaseService.with_session to accept optional role parameter.
        Accepts both Role (org-scoped) and PlatformRole (platform-scoped).
        """
        if session is not None:
            yield cls(session, role=role)
        else:
            async with get_async_session_context_manager() as session:
                yield cls(session, role=role)

    async def _get_webhook_url(self) -> str | None:
        """Fetch the configured audit webhook URL.

        Precedence:
        1. `AUDIT_WEBHOOK_URL` env var
        2. Organization setting `audit_webhook_url`
        """

        from tracecat.settings.service import get_setting_cached

        value = await get_setting_cached("audit_webhook_url")
        if value is None:
            return None
        if not isinstance(value, str):
            self.logger.warning(
                "audit_webhook_url must be a string",
                value=value,
                value_type=type(value),
            )
            return None

        cleaned = value.strip()
        return cleaned or None

    async def _get_custom_headers(self) -> dict[str, str] | None:
        """Fetch the configured custom headers for the audit webhook.

        Note: Uses get_setting (uncached) to ensure changes take effect immediately.
        """
        from tracecat.settings.service import get_setting

        value = await get_setting("audit_webhook_custom_headers", session=self.session)
        if value is None:
            return None
        if not isinstance(value, dict):
            self.logger.warning(
                "audit_webhook_custom_headers must be a dict",
                value_type=type(value),
            )
            return None

        return value

    async def _post_event(self, *, webhook_url: str, payload: AuditEvent) -> None:
        response: httpx.Response | None = None
        try:
            custom_headers = await self._get_custom_headers()

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    webhook_url,
                    json=payload.model_dump(mode="json"),
                    headers=custom_headers,
                )
                response.raise_for_status()
        except Exception as exc:
            self.logger.warning(
                "Failed to deliver audit webhook",
                error=str(exc),
                webhook_url=webhook_url,
                status_code=getattr(response, "status_code", None),
            )

    async def create_event(
        self,
        *,
        resource_type: AuditResourceType,
        action: AuditAction,
        resource_id: uuid.UUID | None = None,
        status: AuditEventStatus = AuditEventStatus.SUCCESS,
    ) -> None:
        # Skip audit if no role or no user_id (non-user operations)
        # Note: PlatformRole.user_id is required, Role.user_id is optional
        if self.role is None or self.role.user_id is None:
            self.logger.debug(
                "Skipping audit log", reason="non_user_role", role=self.role
            )
            return

        webhook_url = await self._get_webhook_url()
        if not webhook_url:
            self.logger.debug("Skipping audit log", reason="webhook_unconfigured")
            return

        actor_label: str | None = None
        try:
            result = await self.session.execute(
                select(User).where(User.id == self.role.user_id)  # pyright: ignore[reportArgumentType]
            )
            user = result.scalar_one_or_none()
            if user:
                actor_label = user.email
        except Exception as exc:
            self.logger.warning("Failed to fetch actor email", error=str(exc))

        # Extract org/workspace IDs - PlatformRole doesn't have these attributes
        # For platform operations, these will be None
        organization_id = getattr(self.role, "organization_id", None)
        workspace_id = getattr(self.role, "workspace_id", None)

        payload = AuditEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type=AuditEventActor.USER,
            actor_id=self.role.user_id,
            actor_label=actor_label,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            status=status,
            ip_address=ctx_client_ip.get(),
        )
        await self._post_event(webhook_url=webhook_url, payload=payload)
        self.logger.debug(
            "Streamed audit event", resource_type=resource_type, action=action
        )
