from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import Coroutine
from functools import partial
from time import monotonic
from typing import Any

import httpx
import orjson
from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from sqlalchemy import select

from tracecat.audit.constants import (
    AUDIT_DELIVERY_STREAM_TTL_SECONDS,
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
from tracecat.settings.service import SettingsService

from .service import (
    AuditWebhookConfig,
    build_audit_webhook_request,
)

_RESTART_BACKOFF_SECONDS = 1.0
_GROUP = "audit-delivery"
_BLOCK_MS = 2_000
_BATCH_SIZE = 100
_MAX_ATTEMPTS = 10
_CIRCUIT_THRESHOLD = 5
_CIRCUIT_TTL_SECONDS = 60
_REQUEST_TIMEOUT_SECONDS = 10.0
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


class AuditDeliveryConsumer:
    """Consume audit delivery streams and POST events to configured webhooks."""

    def __init__(
        self, client: RedisClient, *, consumer_name: str | None = None
    ) -> None:
        self.client = client
        self.group = _GROUP
        self.block_ms = _BLOCK_MS
        self.batch = _BATCH_SIZE
        self.max_attempts = _MAX_ATTEMPTS
        self.stream_ttl = AUDIT_DELIVERY_STREAM_TTL_SECONDS
        self.circuit_threshold = _CIRCUIT_THRESHOLD
        self.circuit_ttl = _CIRCUIT_TTL_SECONDS
        self.timeout = _REQUEST_TIMEOUT_SECONDS
        self.claim_idle_ms = max(self.block_ms * 10, 30_000)
        self.consumer_name = consumer_name or f"{socket.gethostname()}:{os.getpid()}"
        self._pending_check_interval = max(self.claim_idle_ms / 1000.0, 30.0)
        self._stream_tasks: dict[str, asyncio.Task[None]] = {}

    async def run(self) -> None:
        logger.info(
            "Audit delivery consumer started",
            group=self.group,
            consumer=self.consumer_name,
        )
        last_pending_check = monotonic()
        try:
            while True:
                streams = [
                    stream_key
                    for stream_key in await self._discover_streams()
                    if stream_key not in self._stream_tasks
                ]
                if not streams:
                    await asyncio.sleep(self.block_ms / 1000.0)
                else:
                    messages = await self.client.xreadgroup(
                        group_name=self.group,
                        consumer_name=self.consumer_name,
                        streams=dict.fromkeys(streams, ">"),
                        count=self.batch,
                        block=self.block_ms,
                    )
                    self._dispatch_streams(messages)

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
        finally:
            await self._cancel_stream_tasks()

    def _dispatch_streams(
        self,
        messages: Any,
    ) -> None:
        for stream_key, entries in messages:
            if not entries or stream_key in self._stream_tasks:
                continue
            self._start_stream_task(
                stream_key,
                self._handle_stream_entries(stream_key, entries),
            )

    def _start_stream_task(
        self,
        stream_key: str,
        coroutine: Coroutine[Any, Any, None],
    ) -> None:
        task = asyncio.create_task(
            coroutine,
            name=f"audit_delivery:{stream_key}",
        )
        self._stream_tasks[stream_key] = task
        task.add_done_callback(partial(self._stream_task_done, stream_key))

    async def _handle_stream_entries(
        self,
        stream_key: str,
        entries: Any,
    ) -> None:
        for message_id, fields in entries:
            await self._handle_message(stream_key, message_id, fields, attempts=1)

    def _stream_task_done(self, stream_key: str, task: asyncio.Task[None]) -> None:
        if self._stream_tasks.get(stream_key) is task:
            self._stream_tasks.pop(stream_key)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(
                "Audit delivery stream task failed",
                stream_key=stream_key,
                error_type=type(exc).__name__,
            )

    async def _cancel_stream_tasks(self) -> None:
        tasks = list(self._stream_tasks.values())
        self._stream_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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
            if stream_key in self._stream_tasks:
                continue
            self._start_stream_task(
                stream_key,
                self._claim_idle_stream(stream_key),
            )

    async def _claim_idle_stream(self, stream_key: str) -> None:
        pending = await self.client.xpending_range(
            stream_key,
            self.group,
            min_id="-",
            max_id="+",
            count=self.batch,
            idle=self.claim_idle_ms,
        )
        if not pending:
            return

        await self.client.expire(stream_key, self.stream_ttl)

        parsed = parse_audit_delivery_stream_key(stream_key)
        if parsed is None:
            return
        sink, organization_id = parsed
        if await self._is_circuit_open(sink, organization_id):
            return

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
            return

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
        async with get_async_session_bypass_rls_context_manager() as session:
            if sink == "platform":
                values = await self._get_platform_settings(session)
            elif organization_id is not None:
                role = self._get_service_role(organization_id)
                service = SettingsService(session, role=role)
                settings = await service.list_org_settings(
                    keys=set(_AUDIT_WEBHOOK_SETTING_KEYS)
                )
                values, _ = service.get_values_with_decryption_fallback(settings)
            else:
                values = {}

        return self._build_sink_config(values)

    async def _get_platform_settings(self, session) -> dict[str, Any]:
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

    def _build_sink_config(self, values: dict[str, Any]) -> AuditWebhookConfig | None:
        webhook_url = self._clean_string(values.get(_WEBHOOK_URL_KEY))
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

        payload_attribute = self._clean_string(
            values.get(_WEBHOOK_PAYLOAD_ATTRIBUTE_KEY)
        )

        return AuditWebhookConfig(
            webhook_url=webhook_url,
            custom_headers=custom_headers,
            custom_payload=custom_payload,
            verify_ssl=verify_ssl,
            payload_attribute=payload_attribute,
        )

    def _clean_string(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    def _get_service_role(self, organization_id: uuid.UUID) -> Role:
        return Role(
            type="service",
            workspace_id=None,
            organization_id=organization_id,
            user_id=None,
            service_id="tracecat-api",
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-api"],
        )

    async def _is_circuit_open(
        self, sink: AuditSink, organization_id: uuid.UUID | None
    ) -> bool:
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
        if failures >= self.circuit_threshold:
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
