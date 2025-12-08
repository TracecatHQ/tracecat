from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select

from tracecat.audit.enums import AuditEventActor, AuditEventStatus
from tracecat.audit.types import AuditEvent
from tracecat.contexts import ctx_client_ip
from tracecat.db.models import User
from tracecat.service import BaseService


class AuditService(BaseService):
    """Stream user-driven events to an audit webhook if configured."""

    service_name = "audit"

    async def _get_webhook_url(self) -> str | None:
        """Fetch the configured audit webhook URL.

        Precedence:
        1. `AUDIT_WEBHOOK_URL` env var
        2. Organization setting `audit_webhook_url`
        """

        from tracecat.settings.service import SettingsService  # noqa: PLC0415

        settings_service = SettingsService(self.session, role=self.role)
        setting = await settings_service.get_org_setting("audit_webhook_url")
        if setting is None:
            return None

        value = settings_service.get_value(setting)
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

    async def _post_event(self, *, webhook_url: str, payload: AuditEvent) -> None:
        response: httpx.Response | None = None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    webhook_url, json=payload.model_dump(mode="json")
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
        resource_type: str,
        action: str,
        resource_id: uuid.UUID | None = None,
        status: AuditEventStatus = AuditEventStatus.SUCCESS,
    ) -> None:
        role = self.role
        if role is None or role.user_id is None or role.organization_id is None:
            self.logger.debug("Skipping audit log", reason="non_user_role", role=role)
            return

        webhook_url = await self._get_webhook_url()
        if not webhook_url:
            self.logger.debug("Skipping audit log", reason="webhook_unconfigured")
            return

        actor_label: str | None = None
        try:
            result = await self.session.execute(
                select(User).where(User.id == role.user_id)  # pyright: ignore[reportArgumentType]
            )
            user = result.scalar_one_or_none()
            if user:
                actor_label = user.email
        except Exception as exc:
            self.logger.warning("Failed to fetch actor email", error=str(exc))

        payload = AuditEvent(
            organization_id=role.organization_id,
            workspace_id=role.workspace_id,
            actor_type=AuditEventActor.USER,
            actor_id=role.user_id,
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
