"""Fire-and-forget audit webhook delivery with bounded in-memory retries.

Each event is posted by its own asyncio task; transient failures are retried
in process. A retry after a lost response can deliver an exact byte-identical
duplicate. Deliveries are dropped past the pending cap and lost on
process/loop shutdown. Durable, at-least-once delivery arrives with the
ENG-1514 spool.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Self

import httpx
import orjson
from async_lru import alru_cache
from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.audit.enums import AuditEventActor, AuditEventStatus
from tracecat.audit.sanitization import sanitize_audit_metadata
from tracecat.audit.types import (
    AuditAction,
    AuditEvent,
    AuditMetadata,
    AuditMetadataValue,
    AuditResourceType,
    AuditSink,
)
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import PlatformRole, Role
from tracecat.contexts import ctx_request_audit, ctx_role
from tracecat.db.engine import (
    get_async_session_bypass_rls_context_manager,
    get_async_session_context_manager,
)
from tracecat.db.models import PlatformSetting, ServiceAccount, User
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.sanitization import redact_sensitive_text
from tracecat.secrets.encryption import decrypt_value
from tracecat.service import BaseService

# Union type for roles that can be used for audit logging
AuditableRole = Role | PlatformRole


@dataclass(frozen=True)
class _AuditDelivery:
    """A fully resolved audit webhook post, safe to run without a DB session.

    Built synchronously while the request context and session are live, then
    handed to a background worker for fire-and-forget delivery.
    """

    webhook_url: str
    request_payload: dict[str, Any]
    headers: dict[str, str] | None
    verify_ssl: bool
    # Non-sensitive discriminators for failure logging; never the payload contents.
    resource_type: AuditResourceType
    action: AuditAction


# Strong refs to in-flight delivery tasks; done callbacks release them.
_delivery_tasks: set[asyncio.Task[None]] = set()

# Burst-admission bound: pending deliveries past this are dropped. A full
# backlog is a few MB of suspended tasks; worst-case drain ~35 min against a
# hung sink (~33s per slot: 3 timed-out attempts plus backoff, 32 slots).
_MAX_PENDING_DELIVERIES = 2048

# Socket bound: each active post holds one connection/FD for up to the 10s
# httpx timeout; 32 stays in the noise of the smallest deployment FD envelope
# (Fargate default soft ulimit 1024).
_MAX_CONCURRENT_POSTS = 32

# asyncio primitives bind to their loop; keyed per loop and swept alongside the
# closed-loop task sweep in _spawn_delivery.
_post_semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _get_post_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if (sem := _post_semaphores.get(loop)) is None:
        sem = _post_semaphores[loop] = asyncio.Semaphore(_MAX_CONCURRENT_POSTS)
    return sem


# Retry only failures a fresh attempt can plausibly fix; other 4xx are terminal.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_DELIVERY_ATTEMPTS = 3
# Module-level so tests can swap in wait_none().
_RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=10)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return isinstance(exc, httpx.TransportError)


def _spawn_delivery(delivery: _AuditDelivery) -> None:
    """Post the delivery on its own fire-and-forget task."""
    # Tasks stranded on closed (e.g. per-test) loops never ran their done
    # callbacks; evict them and their loops' semaphores so neither container
    # grows across loops.
    for stranded in [t for t in _delivery_tasks if t.get_loop().is_closed()]:
        _delivery_tasks.discard(stranded)
    for closed_loop in [loop for loop in _post_semaphores if loop.is_closed()]:
        del _post_semaphores[closed_loop]
    if len(_delivery_tasks) >= _MAX_PENDING_DELIVERIES:
        # Shed audit load rather than buffer without bound. No payload
        # contents or sink URL on this log line.
        logger.warning(
            "Dropped audit webhook delivery; pending limit reached",
            resource_type=delivery.resource_type,
            action=delivery.action,
            max_pending=_MAX_PENDING_DELIVERIES,
        )
        return
    task = asyncio.get_running_loop().create_task(_deliver(delivery))
    _delivery_tasks.add(task)
    task.add_done_callback(_delivery_tasks.discard)


async def _deliver(delivery: _AuditDelivery) -> None:
    """Post one resolved delivery, gated by the per-loop socket cap.

    Never uses a DB session.
    """
    response: httpx.Response | None = None
    attempts = 0
    try:
        # One slot spans all attempts; the socket itself is only open per post.
        async with _get_post_semaphore():
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(_DELIVERY_ATTEMPTS),
                wait=_RETRY_WAIT,
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    attempts = attempt.retry_state.attempt_number
                    response = None
                    async with httpx.AsyncClient(
                        timeout=10.0, verify=delivery.verify_ssl
                    ) as client:
                        response = await client.post(
                            delivery.webhook_url,
                            json=delivery.request_payload,
                            headers=delivery.headers,
                        )
                        response.raise_for_status()
    except Exception as exc:
        # No exception text or URL on this path: the webhook URL is
        # operator-configured and may carry a credential in its path.
        logger.warning(
            "Failed to deliver audit webhook",
            error_type=type(exc).__name__,
            status_code=response.status_code if response is not None else None,
            resource_type=delivery.resource_type,
            action=delivery.action,
            attempts=attempts,
        )


async def _fetch_platform_setting(key: str) -> Any | None:
    """Read a platform audit setting on a self-managed session; decrypt if needed."""
    async with get_async_session_bypass_rls_context_manager() as session:
        stmt = select(PlatformSetting).where(PlatformSetting.key == key)
        setting = (await session.execute(stmt)).scalar_one_or_none()
    if setting is None:
        return None
    value = setting.value
    if setting.is_encrypted:
        try:
            value = decrypt_value(value, key=get_db_encryption_key())
        except (InvalidToken, ValueError) as exc:
            logger.warning(
                "Failed to decrypt platform audit setting",
                key=key,
                error=redact_sensitive_text(str(exc), redact_emails=True),
            )
            return None
    return orjson.loads(value)


@alru_cache(ttl=30)
async def _get_audit_setting_cached(
    sink: AuditSink,
    organization_id: OrganizationID | None,
    key: str,
    default: Any = None,
) -> Any | None:
    """Cached audit-setting read keyed by (sink, org, key, default).

    Runs on its own session so no request session is captured or hashed; bounded
    30s staleness. ``organization_id`` is ``None`` for the platform sink or
    org-sink calls with no org identity. Decrypted values live in process memory
    only and are never logged.
    """
    logger.debug("Audit setting cache miss", sink=sink, key=key)
    if sink == "platform":
        value = await _fetch_platform_setting(key)
        return default if value is None and default is not None else value

    from tracecat.settings.service import get_setting

    if organization_id is None:
        # No org identity: preserve get_setting(role=None) semantics.
        return default
    # Minimal org-bound role so get_setting opens its own org-scoped session.
    role = Role(
        type="service",
        organization_id=organization_id,
        service_id="tracecat-service",
    )
    return await get_setting(key, role=role, session=None, default=default)


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

    async def _get_audit_setting(self, key: str, *, default: Any = None) -> Any | None:
        """Fetch an audit setting from the active audit sink (30s TTL cache).

        Keyed on (sink, org, key, default) so platform and per-org configs never
        share an entry. The cache runs its own session, so this stays safe inside
        a live request session.
        """
        if self.audit_sink == "organization" and (
            isinstance(self.role, Role) and self.role.organization_id is None
        ):
            # Rare org-sink role without an org id: skip the cache and let
            # get_setting resolve the default org, preserving prior semantics.
            from tracecat.settings.service import get_setting

            return await get_setting(
                key, role=self.role, session=self.session, default=default
            )

        organization_id = (
            self.role.organization_id
            if self.audit_sink == "organization" and isinstance(self.role, Role)
            else None
        )
        return await _get_audit_setting_cached(
            self.audit_sink, organization_id, key, default
        )

    async def _get_webhook_url(self) -> str | None:
        """Fetch the configured audit webhook URL.

        Precedence:
        1. `AUDIT_WEBHOOK_URL` env var
        2. Organization setting `audit_webhook_url`
        """

        value = await self._get_audit_setting("audit_webhook_url")
        if value is None:
            return None
        if not isinstance(value, str):
            self.logger.warning(
                "audit_webhook_url must be a string",
                value_type=type(value),
            )
            return None

        cleaned = value.strip()
        return cleaned or None

    async def _get_custom_headers(self) -> dict[str, str] | None:
        """Fetch the configured custom headers for the audit webhook.

        Note: reads are cached with a 30s TTL, so setting changes take effect
        within that bounded window rather than immediately.
        """
        value = await self._get_audit_setting("audit_webhook_custom_headers")
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
        value = await self._get_audit_setting("audit_webhook_custom_payload")
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
        value = await self._get_audit_setting("audit_webhook_verify_ssl", default=True)
        if not isinstance(value, bool):
            self.logger.warning(
                "audit_webhook_verify_ssl must be a bool",
                value_type=type(value),
            )
            return True
        return value

    async def _get_payload_attribute(self) -> str | None:
        """Fetch optional wrapper attribute for webhook payloads."""
        value = await self._get_audit_setting("audit_webhook_payload_attribute")
        if value is None:
            return None
        if not isinstance(value, str):
            self.logger.warning(
                "audit_webhook_payload_attribute must be a string",
                value_type=type(value),
            )
            return None

        cleaned = value.strip()
        return cleaned or None

    async def _build_delivery(
        self, *, webhook_url: str, payload: AuditEvent
    ) -> _AuditDelivery:
        """Resolve webhook settings and assemble the request body.

        Runs while the request session is live; the returned delivery needs no
        session and is safe to post from a detached background task.
        """
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
        return _AuditDelivery(
            webhook_url=webhook_url,
            request_payload=request_payload,
            headers=custom_headers,
            verify_ssl=verify_ssl,
            resource_type=payload.resource_type,
            action=payload.action,
        )

    async def _post_event(self, *, webhook_url: str, payload: AuditEvent) -> None:
        """Resolve the delivery synchronously, then spawn its delivery task."""
        try:
            delivery = await self._build_delivery(
                webhook_url=webhook_url, payload=payload
            )
        except Exception as exc:
            # Best-effort: a failed settings lookup must never abort the audited
            # operation, and this path never logs the sink URL.
            self.logger.warning(
                "Failed to resolve audit webhook delivery",
                error_type=type(exc).__name__,
            )
            return
        _spawn_delivery(delivery)

    async def _get_actor_label(self) -> str | None:
        if self.role is None:
            return None
        if isinstance(self.role, Role) and self.role.type == "service_account":
            service_account_id = self.role.service_account_id
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
                    "Failed to fetch service account actor name",
                    error=redact_sensitive_text(str(exc), redact_emails=True),
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
            self.logger.warning(
                "Failed to fetch actor email",
                error=redact_sensitive_text(str(exc), redact_emails=True),
            )
        return actor_label

    def _build_payload(
        self,
        *,
        resource_type: AuditResourceType,
        action: AuditAction,
        resource_id: uuid.UUID | None,
        status: AuditEventStatus,
        actor_label: str | None,
        ip_address: str | None,
        user_agent: str | None,
        data: dict[str, AuditMetadataValue] | None,
    ) -> AuditEvent:
        if self.role is None or self.role.actor_id is None:
            raise ValueError("Audit payload requires an auditable actor")
        # Only org-scoped Role carries org/workspace context; PlatformRole lacks it.
        organization_id = (
            self.role.organization_id if isinstance(self.role, Role) else None
        )
        workspace_id = self.role.workspace_id if isinstance(self.role, Role) else None
        actor_type = (
            AuditEventActor.SERVICE_ACCOUNT
            if isinstance(self.role, Role) and self.role.type == "service_account"
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
            ip_address=ip_address,
            user_agent=user_agent,
            data=data,
        )

    async def create_event(
        self,
        *,
        resource_type: AuditResourceType,
        action: AuditAction,
        resource_id: uuid.UUID | None = None,
        status: AuditEventStatus = AuditEventStatus.SUCCESS,
        data: AuditMetadata | None = None,
        include_actor_label: bool = True,
        include_ip_address: bool = True,
        include_user_agent: bool = True,
    ) -> None:
        """Deliver a privacy-bounded audit event when a sink is configured.

        Generic ``data`` is reduced to stable identifiers, changed-field names,
        and a small set of operational discriminators. Unknown fields are
        dropped. An otherwise allowed field is also dropped if its string value
        contains a recognized credential pattern. Raw function arguments and
        return values are never inspected.

        Actor labels, client IPs, and user agents are separate opt-out fields
        because they are useful for attribution and security investigations but
        contain PII or sensitive client metadata. Stable actor and resource IDs
        remain the preferred identifiers.

        Args:
            resource_type: Type of resource affected by the operation.
            action: Operation performed on the resource.
            resource_id: Stable resource ID, when one is available.
            status: Lifecycle state or outcome of the operation.
            data: Explicitly selected operational metadata. Arbitrary resource
                content, names, descriptions, inputs, outputs, bodies, headers,
                and secret-bearing values are not accepted by the audit policy.
            include_actor_label: Whether to resolve and include the actor email
                or service-account name. This field contains PII or user-provided
                identifying text.
            include_ip_address: Whether to include the request client IP from
                context. This field is sensitive security metadata.
            include_user_agent: Whether to include the bounded request
                user-agent from context. This field is sensitive security
                metadata and may identify client software or devices.
        """

        if self.role is None or self.role.actor_id is None:
            self.logger.debug(
                "Skipping audit log",
                reason="non_auditable_role",
                role_type=self.role.type if self.role is not None else None,
            )
            return

        webhook_url = await self._get_webhook_url()
        if not webhook_url:
            self.logger.debug("Skipping audit log", reason="webhook_unconfigured")
            return

        actor_label = await self._get_actor_label() if include_actor_label else None
        request_audit = ctx_request_audit.get()
        payload = self._build_payload(
            resource_type=resource_type,
            action=action,
            resource_id=resource_id,
            status=status,
            actor_label=actor_label,
            ip_address=(
                request_audit.client_ip
                if include_ip_address and request_audit is not None
                else None
            ),
            user_agent=(
                request_audit.user_agent
                if include_user_agent and request_audit is not None
                else None
            ),
            data=sanitize_audit_metadata(data),
        )
        await self._post_event(webhook_url=webhook_url, payload=payload)
        self.logger.debug(
            "Streamed audit event", resource_type=resource_type, action=action
        )
