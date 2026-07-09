from __future__ import annotations

import asyncio
import uuid
from typing import Literal

import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.delivery import resolve_audit_sink_config
from tracecat.audit.enums import AuditEventActor, AuditEventStatus
from tracecat.audit.service import AuditableRole, build_audit_webhook_request
from tracecat.audit.types import AuditEvent, AuditSink
from tracecat.contexts import ctx_client_ip, ctx_request_id, ctx_user_agent
from tracecat.logger import logger

AuditWebhookTestErrorCategory = Literal[
    "receiver_error",
    "timeout",
    "request_error",
]

_AUDIT_WEBHOOK_TEST_TIMEOUT_SECONDS = 5.0


class AuditWebhookNotConfiguredError(Exception):
    """Raised when an audit webhook test is requested without a sink."""


class AuditWebhookTestResult(BaseModel):
    """Result of a synchronous audit webhook test-fire request."""

    ok: bool
    receiver_status_code: int | None = None
    error_category: AuditWebhookTestErrorCategory | None = None


async def test_fire_audit_webhook(
    *,
    sink: AuditSink,
    organization_id: uuid.UUID | None,
    role: AuditableRole,
    session: AsyncSession,
) -> AuditWebhookTestResult:
    sink_config = await resolve_audit_sink_config(
        session=session,
        sink=sink,
        organization_id=organization_id,
    )
    if sink_config is None:
        raise AuditWebhookNotConfiguredError

    event = _build_test_event(sink=sink, organization_id=organization_id, role=role)
    body, headers = build_audit_webhook_request(payload=event, config=sink_config)
    headers = {
        key: value for key, value in headers.items() if key.lower() != "x-tracecat-test"
    }
    headers["X-Tracecat-Test"] = "true"

    try:
        async with asyncio.timeout(_AUDIT_WEBHOOK_TEST_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=_AUDIT_WEBHOOK_TEST_TIMEOUT_SECONDS,
                verify=sink_config.verify_ssl,
            ) as client:
                response = await client.post(
                    sink_config.webhook_url,
                    json=body,
                    headers=headers,
                )
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

    ok = response.is_success
    return AuditWebhookTestResult(
        ok=ok,
        receiver_status_code=response.status_code,
        error_category=None if ok else "receiver_error",
    )


def _build_test_event(
    *,
    sink: AuditSink,
    organization_id: uuid.UUID | None,
    role: AuditableRole,
) -> AuditEvent:
    actor_id = role.actor_id
    if actor_id is None:
        raise ValueError("Audit webhook test requires an auditable actor")
    resource_type = "platform_setting" if sink == "platform" else "organization_setting"
    return AuditEvent(
        organization_id=organization_id,
        workspace_id=getattr(role, "workspace_id", None),
        actor_type=AuditEventActor.USER,
        actor_id=actor_id,
        actor_label=None,
        ip_address=ctx_client_ip.get(),
        user_agent=ctx_user_agent.get(),
        request_id=ctx_request_id.get(),
        resource_type=resource_type,
        resource_id=None,
        action="connect",
        status=AuditEventStatus.SUCCESS,
        data={"test": True},
    )
