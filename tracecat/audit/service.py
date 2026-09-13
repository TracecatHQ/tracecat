"""Fire-and-forget audit webhook delivery with bounded in-memory retries.

Each event is posted by its own asyncio task; transient failures are retried
in process. A retry after a lost response can deliver an exact byte-identical
duplicate. Deliveries are dropped past the pending cap and lost on
process/loop shutdown. Durable, at-least-once delivery arrives with the
ENG-1514 spool.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from base64 import b64encode
from collections.abc import AsyncGenerator, Mapping
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
    AuditWebhookConfig,
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
from tracecat.network import (
    DisallowedUrlError,
    HttpEgressPurpose,
    HttpOrigin,
    configured_http_egress_policy,
)
from tracecat.outbound_http import guarded_async_client
from tracecat.sanitization import redact_sensitive_text
from tracecat.secrets.encryption import decrypt_value
from tracecat.service import BaseService
from tracecat.settings.schemas import AuditSettingsUpdate, AuditWebhookTestResult

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
    # Non-sensitive discriminators for delivery logging; never the payload
    # contents and never the sink URL, whose path may carry a credential.
    resource_type: AuditResourceType
    action: AuditAction
    organization_id: uuid.UUID | None
    workspace_id: uuid.UUID | None


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


def _audit_http_client(*, timeout: float, verify: bool) -> httpx.AsyncClient:
    """Create an audit client whose connections enforce the audit policy."""
    return guarded_async_client(
        configured_http_egress_policy(HttpEgressPurpose.AUDIT),
        timeout=timeout,
        verify=verify,
    )


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
    started = time.perf_counter()
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
                    async with _audit_http_client(
                        timeout=10.0, verify=delivery.verify_ssl
                    ) as client:
                        async with client.stream(
                            "POST",
                            delivery.webhook_url,
                            json=delivery.request_payload,
                            headers=delivery.headers,
                        ) as response:
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
            organization_id=delivery.organization_id,
            workspace_id=delivery.workspace_id,
            attempts=attempts,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return
    # Confirms the sink actually accepted the event, which the spawn-time
    # "Streamed audit event" line cannot. `retried` flags a delivery that
    # recovered from a transient failure. Same discriminators as the failure
    # path: no payload contents, no sink URL.
    logger.info(
        "Delivered audit webhook",
        status_code=response.status_code if response is not None else None,
        resource_type=delivery.resource_type,
        action=delivery.action,
        organization_id=delivery.organization_id,
        workspace_id=delivery.workspace_id,
        attempts=attempts,
        retried=attempts > 1,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )


@dataclass(frozen=True, slots=True)
class _AuditSettingsSnapshot:
    """One database generation of every setting used by a delivery."""

    webhook_url: object = None
    custom_headers: object = None
    custom_payload: object = None
    verify_ssl: object = True
    payload_attribute: object = None


_AUDIT_SETTINGS_KEYS = frozenset(AuditSettingsUpdate.keys())


def _audit_settings_snapshot(
    values: Mapping[str, object],
) -> _AuditSettingsSnapshot:
    return _AuditSettingsSnapshot(
        webhook_url=values.get("audit_webhook_url"),
        custom_headers=values.get("audit_webhook_custom_headers"),
        custom_payload=values.get("audit_webhook_custom_payload"),
        verify_ssl=values.get("audit_webhook_verify_ssl", True),
        payload_attribute=values.get("audit_webhook_payload_attribute"),
    )


async def _fetch_platform_audit_settings() -> _AuditSettingsSnapshot:
    """Read all platform audit settings in one database statement."""
    async with get_async_session_bypass_rls_context_manager() as session:
        stmt = select(PlatformSetting).where(
            PlatformSetting.key.in_(_AUDIT_SETTINGS_KEYS)
        )
        settings = (await session.execute(stmt)).scalars().all()

    values: dict[str, object] = {}
    encryption_key = get_db_encryption_key()
    for setting in settings:
        value = setting.value
        if setting.is_encrypted:
            try:
                value = decrypt_value(value, key=encryption_key)
            except (InvalidToken, ValueError) as exc:
                logger.warning(
                    "Failed to decrypt platform audit setting",
                    key=setting.key,
                    error=redact_sensitive_text(str(exc), redact_emails=True),
                )
                continue
        values[setting.key] = orjson.loads(value)
    return _audit_settings_snapshot(values)


async def _fetch_organization_audit_settings(
    organization_id: OrganizationID,
) -> _AuditSettingsSnapshot:
    """Read all organization audit settings in one database statement."""
    from tracecat.settings.service import SettingsService

    role = Role(
        type="service",
        organization_id=organization_id,
        service_id="tracecat-service",
    )
    async with SettingsService.with_session(role=role) as service:
        settings = await service.list_org_settings(keys=_AUDIT_SETTINGS_KEYS)
        values, _ = service.get_values_with_decryption_fallback(settings)
    return _audit_settings_snapshot(values)


@alru_cache(ttl=30)
async def _get_audit_settings_cached(
    sink: AuditSink,
    organization_id: OrganizationID | None,
) -> _AuditSettingsSnapshot:
    """Cache one atomic settings snapshot by sink and organization."""
    logger.debug("Audit settings cache miss", sink=sink)
    if sink == "platform":
        return await _fetch_platform_audit_settings()
    if organization_id is None:
        return _AuditSettingsSnapshot()
    return await _fetch_organization_audit_settings(organization_id)


def clear_audit_setting_cache() -> None:
    """Make committed audit setting changes visible to the next event."""
    _get_audit_settings_cached.cache_clear()


_AUDIT_WEBHOOK_TEST_TIMEOUT_SECONDS = 5.0

_TEST_HEADER = "X-Tracecat-Test"


class AuditWebhookNotConfiguredError(Exception):
    """Raised when an audit webhook test is requested without a sink."""


class AuditWebhookUrlNotAllowedError(Exception):
    """Raised when a probe URL resolves to a non-public address."""


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

    async def _get_audit_config(self) -> AuditWebhookConfig | None:
        """Resolve one atomically loaded and cached delivery configuration."""
        organization_id = (
            self.role.organization_id
            if self.audit_sink == "organization" and isinstance(self.role, Role)
            else None
        )
        if (
            self.audit_sink == "organization"
            and isinstance(self.role, Role)
            and organization_id is None
        ):
            # Preserve the historical default-organization fallback for the
            # rare service role that is not already bound to an organization.
            from tracecat.api.common import get_default_organization_id

            organization_id = await get_default_organization_id(self.session)

        snapshot = await _get_audit_settings_cached(
            self.audit_sink,
            organization_id,
        )
        return self._config_from_snapshot(snapshot)

    def _config_from_snapshot(
        self, snapshot: _AuditSettingsSnapshot
    ) -> AuditWebhookConfig | None:
        webhook_url = snapshot.webhook_url
        if webhook_url is None:
            return None
        if not isinstance(webhook_url, str):
            self.logger.warning(
                "audit_webhook_url must be a string",
                value_type=type(webhook_url),
            )
            return None
        webhook_url = webhook_url.strip()
        if not webhook_url:
            return None

        custom_headers = snapshot.custom_headers
        if custom_headers is not None and not isinstance(custom_headers, dict):
            self.logger.warning(
                "audit_webhook_custom_headers must be a dict",
                value_type=type(custom_headers),
            )
            custom_headers = None

        custom_payload = snapshot.custom_payload
        if custom_payload is not None and not isinstance(custom_payload, dict):
            self.logger.warning(
                "audit_webhook_custom_payload must be a dict",
                value_type=type(custom_payload),
            )
            custom_payload = None

        verify_ssl = snapshot.verify_ssl
        if not isinstance(verify_ssl, bool):
            self.logger.warning(
                "audit_webhook_verify_ssl must be a bool",
                value_type=type(verify_ssl),
            )
            verify_ssl = True

        payload_attribute = snapshot.payload_attribute
        if payload_attribute is not None and not isinstance(payload_attribute, str):
            self.logger.warning(
                "audit_webhook_payload_attribute must be a string",
                value_type=type(payload_attribute),
            )
            payload_attribute = None
        elif payload_attribute is not None:
            payload_attribute = payload_attribute.strip() or None

        return AuditWebhookConfig(
            webhook_url=webhook_url,
            custom_headers=custom_headers,
            custom_payload=custom_payload,
            verify_ssl=verify_ssl,
            payload_attribute=payload_attribute,
        )

    async def _get_webhook_url(self) -> str | None:
        """Fetch the configured audit webhook URL.

        Precedence:
        1. `AUDIT_WEBHOOK_URL` env var
        2. Organization setting `audit_webhook_url`
        """

        config = await self._get_audit_config()
        return config.webhook_url if config is not None else None

    async def _resolve_config(self, *, webhook_url: str) -> AuditWebhookConfig:
        """Resolve the latest atomic config before a delivery is detached."""
        config = await self._get_audit_config()
        if config is None:
            raise AuditWebhookNotConfiguredError
        if config.webhook_url != webhook_url:
            self.logger.debug("Audit webhook changed while event was assembled")
        return config

    async def _build_delivery(
        self, *, webhook_url: str, payload: AuditEvent
    ) -> _AuditDelivery:
        """Resolve webhook settings and assemble the request body.

        Runs while the request session is live; the returned delivery needs no
        session and is safe to post from a detached background task.
        """
        config = await self._resolve_config(webhook_url=webhook_url)
        return self._assemble_delivery(config=config, payload=payload)

    @staticmethod
    def _assemble_delivery(
        *,
        config: AuditWebhookConfig,
        payload: AuditEvent,
    ) -> _AuditDelivery:
        """Assemble a detached delivery from already-resolved settings."""
        event_payload = payload.model_dump(mode="json")
        if config.custom_payload:
            # Custom fields may enrich an event but cannot replace the canonical
            # audit contract with invalid or misleading values.
            event_payload = {**config.custom_payload, **event_payload}
        request_payload: dict[str, Any]
        if config.payload_attribute:
            request_payload = {config.payload_attribute: event_payload}
        else:
            request_payload = event_payload
        webhook_url = config.webhook_url
        headers = config.custom_headers
        try:
            parsed_url = httpx.URL(webhook_url)
        except (httpx.InvalidURL, TypeError, ValueError):
            pass
        else:
            if parsed_url.username or parsed_url.password:
                # HTTPX previously converted URL userinfo into Basic auth. Keep
                # that behavior while presenting a credential-free URL to the
                # guarded transport and its origin validator.
                credentials = f"{parsed_url.username}:{parsed_url.password}".encode()
                headers = {
                    key: value
                    for key, value in (headers or {}).items()
                    if key.lower() != "authorization"
                }
                headers["Authorization"] = (
                    f"Basic {b64encode(credentials).decode('ascii')}"
                )
                webhook_url = str(parsed_url.copy_with(username=None, password=None))
        return _AuditDelivery(
            webhook_url=webhook_url,
            request_payload=request_payload,
            headers=headers,
            verify_ssl=config.verify_ssl,
            resource_type=payload.resource_type,
            action=payload.action,
            organization_id=payload.organization_id,
            workspace_id=payload.workspace_id,
        )

    @classmethod
    async def probe_webhook(
        cls,
        *,
        sink: AuditSink,
        organization_id: OrganizationID | None,
        role: AuditableRole,
        settings: AuditSettingsUpdate,
    ) -> AuditWebhookTestResult:
        """Post one marked test event to the submitted webhook configuration.

        Probes the caller-provided settings rather than persisted state, so a
        connection can be tested while it is being configured and a save can be
        verified with exactly the values that were just written. Reuses the
        real payload assembly and delivery headers so a passing probe exercises
        the same request shape as live delivery. Never touches the database.
        """
        webhook_url = (settings.audit_webhook_url or "").strip()
        if not webhook_url:
            raise AuditWebhookNotConfiguredError
        payload_attribute = settings.audit_webhook_payload_attribute
        config = AuditWebhookConfig(
            webhook_url=webhook_url,
            custom_headers=settings.audit_webhook_custom_headers,
            custom_payload=settings.audit_webhook_custom_payload,
            verify_ssl=settings.audit_webhook_verify_ssl,
            payload_attribute=(
                payload_attribute.strip() or None
                if payload_attribute is not None
                else None
            ),
        )

        try:
            async with asyncio.timeout(_AUDIT_WEBHOOK_TEST_TIMEOUT_SECONDS):
                event = cls._build_test_event(
                    sink=sink, organization_id=organization_id, role=role
                )
                delivery = cls._assemble_delivery(config=config, payload=event)
                try:
                    # Normalize structural errors here for a stable 400. DNS is
                    # resolved and pinned only inside the guarded connection.
                    HttpOrigin.from_url(delivery.webhook_url)
                except DisallowedUrlError as exc:
                    raise AuditWebhookUrlNotAllowedError from exc

                headers = {
                    key: value
                    for key, value in (delivery.headers or {}).items()
                    if key.lower() != _TEST_HEADER.lower()
                }
                headers[_TEST_HEADER] = "true"

                # Probes share the process-wide socket budget with live
                # delivery; one that cannot get a slot within the wall clock
                # times out instead of queueing without bound.
                async with _get_post_semaphore():
                    async with _audit_http_client(
                        timeout=_AUDIT_WEBHOOK_TEST_TIMEOUT_SECONDS,
                        verify=delivery.verify_ssl,
                    ) as client:
                        # Stream so the receiver's body is never buffered; the
                        # probe only ever reads the status line.
                        async with client.stream(
                            "POST",
                            delivery.webhook_url,
                            json=delivery.request_payload,
                            headers=headers,
                        ) as response:
                            ok = response.is_success
                            receiver_status_code = response.status_code
        except DisallowedUrlError as exc:
            raise AuditWebhookUrlNotAllowedError from exc
        except (TimeoutError, httpx.TimeoutException) as exc:
            logger.warning(
                "Audit webhook test timed out",
                sink=sink,
                error_type=type(exc).__name__,
            )
            return AuditWebhookTestResult(ok=False, error_category="timeout")
        except httpx.RequestError as exc:
            logger.warning(
                "Audit webhook test request failed",
                sink=sink,
                error_type=type(exc).__name__,
            )
            return AuditWebhookTestResult(ok=False, error_category="request_error")

        return AuditWebhookTestResult(
            ok=ok,
            receiver_status_code=receiver_status_code,
            error_category=None if ok else "receiver_error",
        )

    @staticmethod
    def _build_test_event(
        *,
        sink: AuditSink,
        organization_id: OrganizationID | None,
        role: AuditableRole,
    ) -> AuditEvent:
        """Build the clearly-marked synthetic event a probe delivers."""
        actor_id = role.actor_id
        if actor_id is None:
            raise ValueError("Audit webhook test requires an auditable actor")
        resource_type = (
            "platform_setting" if sink == "platform" else "organization_setting"
        )
        request_audit = ctx_request_audit.get()
        return AuditEvent(
            organization_id=organization_id,
            workspace_id=role.workspace_id if isinstance(role, Role) else None,
            actor_type=AuditEventActor.USER,
            actor_id=actor_id,
            actor_label=None,
            ip_address=request_audit.client_ip if request_audit is not None else None,
            user_agent=request_audit.user_agent if request_audit is not None else None,
            resource_type=resource_type,
            resource_id=None,
            action="connect",
            status=AuditEventStatus.SUCCESS,
            data={"test": True},
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
