from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Self

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.enums import AuditEventActor, AuditEventStatus
from tracecat.audit.types import AuditAction, AuditEvent, AuditResourceType
from tracecat.auth.types import PlatformRole, Role
from tracecat.contexts import ctx_client_ip, ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import ServiceAccount, User
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

    async def _get_custom_payload(self) -> dict[str, Any] | None:
        """Fetch the configured custom payload for the audit webhook."""
        from tracecat.settings.service import get_setting

        value = await get_setting("audit_webhook_custom_payload", session=self.session)
        if value is None:
            return None
        if not isinstance(value, dict):
            self.logger.warning(
                "audit_webhook_custom_payload must be a dict",
                value_type=type(value),
            )
            return None
        return value

    async def _get_verify_ssl(self) -> bool:
        """Fetch SSL verification setting for audit webhook requests."""
        from tracecat.settings.service import get_setting

        value = await get_setting(
            "audit_webhook_verify_ssl", session=self.session, default=True
        )
        if not isinstance(value, bool):
            self.logger.warning(
                "audit_webhook_verify_ssl must be a bool",
                value=value,
                value_type=type(value),
            )
            return True
        return value

    async def _get_payload_attribute(self) -> str | None:
        """Fetch optional wrapper attribute for webhook payloads."""
        from tracecat.settings.service import get_setting

        value = await get_setting(
            "audit_webhook_payload_attribute", session=self.session
        )
        if value is None:
            return None
        if not isinstance(value, str):
            self.logger.warning(
                "audit_webhook_payload_attribute must be a string",
                value=value,
                value_type=type(value),
            )
            return None

        cleaned = value.strip()
        return cleaned or None

    async def _post_event(self, *, webhook_url: str, payload: AuditEvent) -> None:
        response: httpx.Response | None = None
        try:
            custom_headers = await self._get_custom_headers()
            custom_payload = await self._get_custom_payload()
            verify_ssl = await self._get_verify_ssl()
            payload_attribute = await self._get_payload_attribute()
            event_payload = payload.model_dump(mode="json")
            if custom_payload:
                event_payload = {**event_payload, **custom_payload}
            request_payload: dict[str, Any]
            if payload_attribute:
                request_payload = {payload_attribute: event_payload}
            else:
                request_payload = event_payload

            async with httpx.AsyncClient(timeout=10.0, verify=verify_ssl) as client:
                response = await client.post(
                    webhook_url,
                    json=request_payload,
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

    async def _get_actor_label(self) -> str | None:
        if self.role is None:
            return None
        if getattr(self.role, "type", None) == "service_account":
            service_account_id = getattr(self.role, "service_account_id", None)
            if service_account_id is None:
                return None
            try:
                result = await self.session.execute(
                    select(ServiceAccount).where(
                        ServiceAccount.id == service_account_id
                    )
                )
                if (service_account := result.scalar_one_or_none()) is not None:
                    return service_account.name
            except Exception as exc:
                self.logger.warning(
                    "Failed to fetch service account actor name", error=str(exc)
                )
            return None
        if self.role.user_id is None:
            return None
        actor_label: str | None = None
        try:
            result = await self.session.execute(
                select(User).where(User.id == self.role.user_id)  # pyright: ignore[reportArgumentType]
            )
            if (user := result.scalar_one_or_none()) is not None:
                actor_label = user.email
        except Exception as exc:
            self.logger.warning("Failed to fetch actor email", error=str(exc))
        return actor_label

    def _build_payload(
        self,
        *,
        resource_type: AuditResourceType,
        action: AuditAction,
        resource_id: uuid.UUID | None,
        status: AuditEventStatus,
        actor_label: str | None,
        data: dict[str, Any] | None,
    ) -> AuditEvent:
        if self.role is None or self.role.actor_id is None:
            raise ValueError("Audit payload requires an auditable actor")
        organization_id = getattr(self.role, "organization_id", None)
        workspace_id = getattr(self.role, "workspace_id", None)
        actor_type = (
            AuditEventActor.SERVICE_ACCOUNT
            if getattr(self.role, "type", None) == "service_account"
            else AuditEventActor.USER
        )
        return AuditEvent(
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_type=actor_type,
            actor_id=self.role.actor_id,
            actor_label=actor_label,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            status=status,
            ip_address=ctx_client_ip.get(),
            data=data,
        )

    async def create_event(
        self,
        *,
        resource_type: AuditResourceType,
        action: AuditAction,
        resource_id: uuid.UUID | None = None,
        status: AuditEventStatus = AuditEventStatus.SUCCESS,
        data: dict[str, Any] | None = None,
    ) -> None:
        if self.role is None or getattr(self.role, "actor_id", None) is None:
            self.logger.debug(
                "Skipping audit log", reason="non_auditable_role", role=self.role
            )
            return

        webhook_url = await self._get_webhook_url()
        if not webhook_url:
            self.logger.debug("Skipping audit log", reason="webhook_unconfigured")
            return

        actor_label = await self._get_actor_label()
        payload = self._build_payload(
            resource_type=resource_type,
            action=action,
            resource_id=resource_id,
            status=status,
            actor_label=actor_label,
            data=data,
        )
        await self._post_event(webhook_url=webhook_url, payload=payload)
        self.logger.debug(
            "Streamed audit event", resource_type=resource_type, action=action
        )
