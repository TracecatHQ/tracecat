from __future__ import annotations

import asyncio
import os
import socket
import uuid
from time import monotonic
from typing import Any

import httpx
import orjson
from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.audit.constants import (
    AUDIT_DELIVERY_GROUP,
    AUDIT_DELIVERY_STREAMS_KEY,
    parse_audit_delivery_stream_key,
)
from tracecat.audit.types import AuditEvent, AuditSink
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import PlatformSetting
from tracecat.logger import logger
from tracecat.redis.client import RedisClient, get_redis_client
from tracecat.secrets.encryption import decrypt_value
from tracecat.settings.service import get_setting

from .service import (
    AuditWebhookConfig,
    build_audit_webhook_request,
)

_CONFIG_CACHE_TTL_SECONDS = 60.0
_RESTART_BACKOFF_SECONDS = 1.0
_WEBHOOK_URL_KEY = "audit_webhook_url"
_WEBHOOK_CUSTOM_HEADERS_KEY = "audit_webhook_custom_headers"
_WEBHOOK_CUSTOM_PAYLOAD_KEY = "audit_webhook_custom_payload"
_WEBHOOK_VERIFY_SSL_KEY = "audit_webhook_verify_ssl"
_WEBHOOK_PAYLOAD_ATTRIBUTE_KEY = "audit_webhook_payload_attribute"
_AUDIT_WEBHOOK_SETTING_KEYS = (
    _WEBHOOK_URL_KEY,
    _WEBHOOK_CUSTOM_HEADERS_KEY,
    _WEBHOOK_CUSTOM_PAYLOAD_KEY,
    _WEBHOOK_VERIFY_SSL_KEY,
    _WEBHOOK_PAYLOAD_ATTRIBUTE_KEY,
)


async def resolve_audit_sink_config(
    session: AsyncSession,
    sink: AuditSink,
    organization_id: uuid.UUID | None,
) -> AuditWebhookConfig | None:
    """Resolve and validate the webhook config used for audit delivery."""
    values = await get_audit_sink_settings(session, sink, organization_id)
    return build_audit_sink_config(values)


async def get_audit_sink_settings(
    session: AsyncSession,
    sink: AuditSink,
    organization_id: uuid.UUID | None,
) -> dict[str, Any]:
    if sink == "platform":
        return await get_platform_audit_sink_settings(session)
    if organization_id is None:
        return {}

    role = get_audit_sink_service_role(organization_id)
    return {
        _WEBHOOK_URL_KEY: await get_setting(
            _WEBHOOK_URL_KEY, role=role, session=session
        ),
        _WEBHOOK_CUSTOM_HEADERS_KEY: await get_setting(
            _WEBHOOK_CUSTOM_HEADERS_KEY, role=role, session=session
        ),
        _WEBHOOK_CUSTOM_PAYLOAD_KEY: await get_setting(
            _WEBHOOK_CUSTOM_PAYLOAD_KEY, role=role, session=session
        ),
        _WEBHOOK_VERIFY_SSL_KEY: await get_setting(
            _WEBHOOK_VERIFY_SSL_KEY,
            role=role,
            session=session,
            default=True,
        ),
        _WEBHOOK_PAYLOAD_ATTRIBUTE_KEY: await get_setting(
            _WEBHOOK_PAYLOAD_ATTRIBUTE_KEY, role=role, session=session
        ),
    }


async def get_platform_audit_sink_settings(
    session: AsyncSession,
) -> dict[str, Any]:
    result = await session.execute(
        select(PlatformSetting).where(
            PlatformSetting.key.in_(_AUDIT_WEBHOOK_SETTING_KEYS)
        )
    )
    values: dict[str, Any] = {}
    for setting in result.scalars().all():
        value = setting.value
        if setting.is_encrypted:
            try:
                value = decrypt_value(value, key=get_db_encryption_key())
            except (InvalidToken, ValueError) as exc:
                logger.warning(
                    "Failed to decrypt platform audit setting",
                    key=setting.key,
                    error_type=type(exc).__name__,
                )
                continue
        values[setting.key] = orjson.loads(value)
    return values


def build_audit_sink_config(values: dict[str, Any]) -> AuditWebhookConfig | None:
    webhook_url = clean_audit_sink_string(values.get(_WEBHOOK_URL_KEY))
    if webhook_url is None:
        return None

    custom_headers = values.get(_WEBHOOK_CUSTOM_HEADERS_KEY)
    if custom_headers is not None and not isinstance(custom_headers, dict):
        logger.warning("audit_webhook_custom_headers must be a dict")
        custom_headers = None

    custom_payload = values.get(_WEBHOOK_CUSTOM_PAYLOAD_KEY)
    if custom_payload is not None and not isinstance(custom_payload, dict):
        logger.warning("audit_webhook_custom_payload must be a dict")
        custom_payload = None

    verify_ssl = values.get(_WEBHOOK_VERIFY_SSL_KEY, True)
    if not isinstance(verify_ssl, bool):
        logger.warning("audit_webhook_verify_ssl must be a bool")
        verify_ssl = True

    payload_attribute = clean_audit_sink_string(
        values.get(_WEBHOOK_PAYLOAD_ATTRIBUTE_KEY)
    )

    return AuditWebhookConfig(
        webhook_url=webhook_url,
        custom_headers=custom_headers,
        custom_payload=custom_payload,
        verify_ssl=verify_ssl,
        payload_attribute=payload_attribute,
    )


def clean_audit_sink_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def get_audit_sink_service_role(organization_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        workspace_id=None,
        organization_id=organization_id,
        user_id=None,
        service_id="tracecat-api",
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-api"],
    )


class AuditDeliveryConsumer:
    """Consume audit delivery streams and POST events to configured webhooks."""

    def __init__(
        self, client: RedisClient, *, consumer_name: str | None = None
    ) -> None:
        self.client = client
        self.group = AUDIT_DELIVERY_GROUP
        self.block_ms = config.TRACECAT__AUDIT_DELIVERY_BLOCK_MS
        self.batch = config.TRACECAT__AUDIT_DELIVERY_BATCH
        self.max_attempts = config.TRACECAT__AUDIT_DELIVERY_MAX_ATTEMPTS
        self.stream_ttl = config.TRACECAT__AUDIT_DELIVERY_TTL_SECONDS
        self.circuit_threshold = config.TRACECAT__AUDIT_DELIVERY_CIRCUIT_THRESHOLD
        self.circuit_ttl = config.TRACECAT__AUDIT_DELIVERY_CIRCUIT_TTL_SECONDS
        self.timeout = config.TRACECAT__AUDIT_DELIVERY_TIMEOUT_SECONDS
        self.claim_idle_ms = max(self.block_ms * 10, 30_000)
        self.consumer_name = consumer_name or f"{socket.gethostname()}:{os.getpid()}"
        self._pending_check_interval = max(self.claim_idle_ms / 1000.0, 30.0)
        self._config_cache: dict[
            tuple[AuditSink, uuid.UUID | None],
            tuple[float, AuditWebhookConfig | None],
        ] = {}

    async def run(self) -> None:
        if not config.TRACECAT__AUDIT_DELIVERY_ENABLED:
            logger.info("Audit delivery disabled; skipping consumer")
            return

        logger.info(
            "Audit delivery consumer started",
            group=self.group,
            consumer=self.consumer_name,
        )
        last_pending_check = monotonic()
        try:
            while True:
                streams = await self._discover_streams()
                if not streams:
                    await asyncio.sleep(self.block_ms / 1000.0)
                    continue

                messages = await self.client.xreadgroup(
                    group_name=self.group,
                    consumer_name=self.consumer_name,
                    streams=dict.fromkeys(streams, ">"),
                    count=self.batch,
                    block=self.block_ms,
                )
                if messages:
                    for stream_key, entries in messages:
                        for message_id, fields in entries:
                            await self._handle_message(
                                stream_key, message_id, fields, attempts=1
                            )

                now = monotonic()
                if now - last_pending_check >= self._pending_check_interval:
                    await self._claim_idle_messages()
                    last_pending_check = now
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            logger.info("Audit delivery consumer cancelled")
            raise
        except Exception as exc:
            logger.error(
                "Audit delivery consumer stopped due to error",
                error_type=type(exc).__name__,
            )
            raise

    async def _discover_streams(self) -> list[str]:
        streams: list[str] = []
        for stream_key in sorted(
            await self.client.smembers(AUDIT_DELIVERY_STREAMS_KEY)
        ):
            parsed = parse_audit_delivery_stream_key(stream_key)
            if parsed is None:
                await self.client.srem(AUDIT_DELIVERY_STREAMS_KEY, stream_key)
                continue
            if not await self.client.exists(stream_key):
                await self.client.srem(AUDIT_DELIVERY_STREAMS_KEY, stream_key)
                continue
            await self.client.xgroup_create(
                stream_key,
                self.group,
                id="0",
                ignore_busygroup=True,
            )
            streams.append(stream_key)
        return streams

    async def _handle_message(
        self,
        stream_key: str,
        message_id: str,
        fields: dict[str, str],
        *,
        attempts: int,
    ) -> None:
        try:
            should_ack = await self._process_message(
                stream_key, message_id, fields, attempts
            )
        except Exception as exc:
            logger.warning(
                "Audit delivery message failed unexpectedly",
                stream_key=stream_key,
                message_id=message_id,
                error_type=type(exc).__name__,
            )
            return
        if should_ack:
            await self.client.xack(stream_key, self.group, [message_id])

    async def _process_message(
        self,
        stream_key: str,
        message_id: str,
        fields: dict[str, str],
        attempts: int,
    ) -> bool:
        parsed = parse_audit_delivery_stream_key(stream_key)
        if parsed is None:
            logger.warning("Malformed audit delivery stream key", stream_key=stream_key)
            return True
        sink, organization_id = parsed

        event_json = fields.get("event")
        if event_json is None:
            logger.warning(
                "Malformed audit delivery message",
                stream_key=stream_key,
                message_id=message_id,
            )
            return True
        try:
            event = AuditEvent.model_validate_json(event_json)
        except (ValidationError, ValueError) as exc:
            logger.warning(
                "Invalid audit delivery event",
                stream_key=stream_key,
                message_id=message_id,
                error_type=type(exc).__name__,
            )
            return True

        if await self._is_circuit_open(sink, organization_id):
            logger.debug(
                "Skipping audit delivery while circuit is open",
                event_id=str(event.id),
                sink=sink,
                organization_id=str(organization_id) if organization_id else None,
            )
            return False

        sink_config = await self._get_sink_config(sink, organization_id)
        if sink_config is None:
            logger.info(
                "Dropping audit event for unconfigured sink",
                event_id=str(event.id),
                sink=sink,
                organization_id=str(organization_id) if organization_id else None,
            )
            return True

        try:
            await self._deliver_event(event, sink_config)
            await self._clear_sink_failure(sink, organization_id)
            return True
        except Exception as exc:
            await self._record_sink_failure(sink, organization_id)
            if attempts >= self.max_attempts:
                logger.warning(
                    "Dropping audit event after max delivery attempts",
                    event_id=str(event.id),
                    attempts=attempts,
                    **self._delivery_error_fields(exc),
                )
                return True
            logger.warning(
                "Audit webhook delivery failed",
                event_id=str(event.id),
                attempts=attempts,
                **self._delivery_error_fields(exc),
            )
            return False

    async def _deliver_event(
        self, event: AuditEvent, sink_config: AuditWebhookConfig
    ) -> None:
        body, headers = build_audit_webhook_request(payload=event, config=sink_config)
        async with httpx.AsyncClient(
            timeout=self.timeout, verify=sink_config.verify_ssl
        ) as client:
            response = await client.post(
                sink_config.webhook_url,
                json=body,
                headers=headers,
            )
            response.raise_for_status()

    def _delivery_error_fields(self, exc: Exception) -> dict[str, Any]:
        fields: dict[str, Any] = {"error_type": type(exc).__name__}
        response = getattr(exc, "response", None)
        if isinstance(response, httpx.Response):
            fields["status_code"] = response.status_code
        return fields

    async def _claim_idle_messages(self) -> None:
        for stream_key in await self._discover_streams():
            pending = await self.client.xpending_range(
                stream_key,
                self.group,
                min_id="-",
                max_id="+",
                count=self.batch,
                idle=self.claim_idle_ms,
            )
            if not pending:
                continue

            await self.client.expire(stream_key, self.stream_ttl)

            parsed = parse_audit_delivery_stream_key(stream_key)
            if parsed is None:
                continue
            sink, organization_id = parsed
            if await self._is_circuit_open(sink, organization_id):
                continue

            delivery_counts = self._pending_delivery_counts(pending)
            exhausted_ids = [
                message_id
                for message_id, attempts in delivery_counts.items()
                if attempts >= self.max_attempts
            ]
            if exhausted_ids:
                logger.warning(
                    "Dropping audit events after max delivery attempts",
                    stream_key=stream_key,
                    count=len(exhausted_ids),
                )
                await self.client.xack(stream_key, self.group, exhausted_ids)

            claim_ids = [
                message_id
                for message_id, attempts in delivery_counts.items()
                if attempts < self.max_attempts
            ]
            if not claim_ids:
                continue

            claimed = await self.client.xclaim(
                stream_key,
                self.group,
                self.consumer_name,
                self.claim_idle_ms,
                claim_ids,
            )
            for message_id, fields in claimed:
                await self._handle_message(
                    stream_key,
                    message_id,
                    fields,
                    attempts=delivery_counts.get(message_id, 0) + 1,
                )

    def _pending_delivery_counts(self, pending: list[dict[str, Any]]) -> dict[str, int]:
        return {
            str(entry["message_id"]): int(entry["times_delivered"]) for entry in pending
        }

    async def _get_sink_config(
        self, sink: AuditSink, organization_id: uuid.UUID | None
    ) -> AuditWebhookConfig | None:
        cache_key = (sink, organization_id)
        cached = self._config_cache.get(cache_key)
        now = monotonic()
        if cached is not None:
            expires_at, sink_config = cached
            if expires_at > now:
                return sink_config

        async with get_async_session_bypass_rls_context_manager() as session:
            sink_config = await resolve_audit_sink_config(
                session=session,
                sink=sink,
                organization_id=organization_id,
            )
        self._config_cache[cache_key] = (now + _CONFIG_CACHE_TTL_SECONDS, sink_config)
        return sink_config

    async def _is_circuit_open(
        self, sink: AuditSink, organization_id: uuid.UUID | None
    ) -> bool:
        if self.circuit_threshold <= 0:
            return False
        raw = await self.client.get(self._circuit_key(sink, organization_id))
        if raw is None:
            return False
        try:
            return int(raw) >= self.circuit_threshold
        except ValueError:
            return False

    async def _record_sink_failure(
        self, sink: AuditSink, organization_id: uuid.UUID | None
    ) -> None:
        failures = await self.client.incr_with_expire(
            self._circuit_key(sink, organization_id),
            expire_seconds=self.circuit_ttl,
        )
        if failures >= self.circuit_threshold > 0:
            logger.warning(
                "Audit delivery circuit opened",
                sink=sink,
                organization_id=str(organization_id) if organization_id else None,
                failures=failures,
            )

    async def _clear_sink_failure(
        self, sink: AuditSink, organization_id: uuid.UUID | None
    ) -> None:
        await self.client.delete(self._circuit_key(sink, organization_id))

    def _circuit_key(self, sink: AuditSink, organization_id: uuid.UUID | None) -> str:
        org_key = "_" if organization_id is None else str(organization_id)
        return f"audit:delivery:circuit:{sink}:{org_key}"


async def start_audit_delivery_consumer() -> None:
    while True:
        try:
            client = await get_redis_client()
            consumer = AuditDeliveryConsumer(client)
            await consumer.run()
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Restarting audit delivery consumer after error",
                error_type=type(exc).__name__,
            )
            await asyncio.sleep(_RESTART_BACKOFF_SECONDS)
