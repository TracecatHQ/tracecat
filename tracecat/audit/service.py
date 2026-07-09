from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Self, cast

import orjson
from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.constants import (
    AUDIT_DELIVERY_STREAM_TTL_SECONDS,
    AUDIT_DELIVERY_STREAMS_KEY,
    audit_delivery_stream_key,
)
from tracecat.audit.enums import AuditEventActor, AuditEventStatus
from tracecat.audit.types import AuditAction, AuditEvent, AuditResourceType, AuditSink
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import PlatformRole, Role
from tracecat.contexts import (
    ctx_client_ip,
    ctx_request_id,
    ctx_role,
    ctx_user_agent,
)
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import PlatformSetting, ServiceAccount, User
from tracecat.redis.client import get_redis_client
from tracecat.secrets.encryption import decrypt_value
from tracecat.service import BaseService

# Union type for roles that can be used for audit logging
AuditableRole = Role | PlatformRole
_AUDIT_DELIVERY_STREAM_MAXLEN = 30_000


@dataclass(frozen=True)
class AuditWebhookConfig:
    webhook_url: str
    custom_headers: dict[str, str] | None = None
    custom_payload: dict[str, Any] | None = None
    verify_ssl: bool = True
    payload_attribute: str | None = None


def build_audit_webhook_request(
    *,
    payload: AuditEvent,
    config: AuditWebhookConfig,
) -> tuple[dict[str, Any], dict[str, str]]:
    event_payload = payload.model_dump(mode="json")
    if config.custom_payload:
        custom_payload = {
            key: value
            for key, value in config.custom_payload.items()
            if key not in {"id", "version"}
        }
        event_payload = {**event_payload, **custom_payload}

    if config.payload_attribute:
        request_payload = {config.payload_attribute: event_payload}
    else:
        request_payload = event_payload

    headers = {
        **(config.custom_headers or {}),
        "X-Tracecat-Event-Id": str(payload.id),
        "X-Tracecat-Timestamp": payload.created_at.isoformat(),
    }
    return request_payload, headers


class AuditService(BaseService):
    """Stream user-driven events to an audit webhook if configured.

    This service accepts an optional role to support both:
    - Platform operations (PlatformRole - no org/workspace context)
    - Org-scoped operations (Role - with org context)

    The role is used for audit attribution (who performed the action).
    """

    service_name = "audit"
    role: AuditableRole | None

    def __init__(
        self,
        session: AsyncSession,
        role: AuditableRole | None = None,
        *,
        audit_sink: AuditSink | None = None,
    ):
        super().__init__(session)
        self.role = role or ctx_role.get()
        self.audit_sink = audit_sink or (
            "platform" if isinstance(self.role, PlatformRole) else "organization"
        )
        # Don't require organization_id - platform ops won't have one

    @classmethod
    @asynccontextmanager
    async def with_session(
        cls,
        role: AuditableRole | None = None,
        *,
        session: AsyncSession | None = None,
        audit_sink: AuditSink | None = None,
    ) -> AsyncGenerator[Self, None]:
        """Create an AuditService instance with a database session.

        Override BaseService.with_session to accept optional role parameter.
        Accepts both Role (org-scoped) and PlatformRole (platform-scoped).
        """
        if session is not None:
            yield cls(session, role=role, audit_sink=audit_sink)
        else:
            async with get_async_session_context_manager() as session:
                yield cls(session, role=role, audit_sink=audit_sink)

    async def _get_platform_setting(self, key: str) -> Any | None:
        """Fetch a platform setting for platform-scoped audit delivery."""
        stmt = select(PlatformSetting).where(PlatformSetting.key == key)
        setting = (await self.session.execute(stmt)).scalar_one_or_none()
        if setting is None:
            return None

        value = setting.value
        if setting.is_encrypted:
            try:
                value = decrypt_value(value, key=get_db_encryption_key())
            except (InvalidToken, ValueError) as exc:
                self.logger.warning(
                    "Failed to decrypt platform audit setting",
                    key=key,
                    error=str(exc),
                )
                return None
        return orjson.loads(value)

    async def _get_audit_setting(self, key: str, *, default: Any = None) -> Any | None:
        """Fetch an audit setting from the active audit sink."""
        if self.audit_sink == "platform":
            value = await self._get_platform_setting(key)
            return default if value is None and default is not None else value

        from tracecat.settings.service import get_setting

        return await get_setting(
            key,
            role=self.role if isinstance(self.role, Role) else None,
            session=self.session,
            default=default,
        )

    async def _get_webhook_url(self) -> str | None:
        """Fetch the configured audit webhook URL."""
        value = await self._get_audit_setting("audit_webhook_url")
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

    async def _has_configured_sink(self) -> bool:
        return bool(await self._get_webhook_url())

    async def _publish_event(self, payload: AuditEvent) -> None:
        stream_key = audit_delivery_stream_key(
            cast(AuditSink, self.audit_sink), payload.organization_id
        )
        redis = await get_redis_client()
        await redis.publish_audit(
            stream_key,
            {"event": payload.model_dump_json()},
            discovery_key=AUDIT_DELIVERY_STREAMS_KEY,
            maxlen=_AUDIT_DELIVERY_STREAM_MAXLEN,
            approximate=True,
            expire_seconds=AUDIT_DELIVERY_STREAM_TTL_SECONDS,
        )

    async def create_event(
        self,
        *,
        resource_type: AuditResourceType,
        action: AuditAction,
        resource_id: str | uuid.UUID | None = None,
        parent_resource_type: AuditResourceType | None = None,
        parent_resource_id: str | uuid.UUID | None = None,
        status: AuditEventStatus = AuditEventStatus.SUCCESS,
        data: dict[str, Any] | None = None,
        include_actor_label: bool = True,
        include_ip_address: bool = True,
    ) -> None:
        role = self.role
        if role is None or role.actor_id is None:
            self.logger.debug(
                "Skipping audit log", reason="non_auditable_role", role=role
            )
            return

        if not await self._has_configured_sink():
            self.logger.debug("Skipping audit log", reason="webhook_unconfigured")
            return

        actor_label = await self._get_actor_label() if include_actor_label else None
        actor_type = (
            AuditEventActor.SERVICE_ACCOUNT
            if getattr(role, "type", None) == "service_account"
            else AuditEventActor.USER
        )
        payload = AuditEvent(
            organization_id=getattr(role, "organization_id", None),
            workspace_id=getattr(role, "workspace_id", None),
            actor_type=actor_type,
            actor_id=role.actor_id,
            actor_label=actor_label,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            parent_resource_type=parent_resource_type,
            parent_resource_id=str(parent_resource_id)
            if parent_resource_id is not None
            else None,
            action=action,
            status=status,
            ip_address=ctx_client_ip.get() if include_ip_address else None,
            user_agent=ctx_user_agent.get(),
            request_id=ctx_request_id.get(),
            data=data,
        )
        try:
            await self._publish_event(payload)
        except Exception as exc:
            self.logger.warning(
                "Failed to enqueue audit event",
                event_id=str(payload.id),
                audit_sink=self.audit_sink,
                error=str(exc),
            )
            return
        self.logger.debug(
            "Queued audit event",
            event_id=str(payload.id),
            resource_type=resource_type,
            action=action,
        )
