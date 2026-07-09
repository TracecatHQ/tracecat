from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.contexts import ctx_client_ip
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client

AUDIT_CREDENTIAL_USAGE_IDLE_SECONDS = 900
CredentialUsageType = Literal["service_account_api_key", "mcp_personal_access_token"]
_AUDIT_USAGE_TASKS: set[asyncio.Task[None]] = set()


def audit_credential_usage_marker_key(
    *,
    credential_type: CredentialUsageType,
    credential_key_id: str,
    source_ip: str | None,
) -> str:
    return f"audit:usage:{credential_type}:{credential_key_id}:{source_ip or '-'}"


async def emit_credential_usage_audit(
    *,
    role: Role,
    credential_type: CredentialUsageType,
    credential_key_id: str,
    resource_id: str | uuid.UUID,
    source_ip: str | None = None,
) -> None:
    source_ip = source_ip if source_ip is not None else ctx_client_ip.get()
    marker_key = audit_credential_usage_marker_key(
        credential_type=credential_type,
        credential_key_id=credential_key_id,
        source_ip=source_ip,
    )

    try:
        redis = await get_redis_client()
        previous = await redis.set_audit_get(
            marker_key,
            "1",
            expire_seconds=AUDIT_CREDENTIAL_USAGE_IDLE_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "Skipping credential usage audit after Redis marker failure",
            credential_type=credential_type,
            error=str(exc),
        )
        return

    if previous is not None:
        return

    # Marker-before-emit is intentional: failed delivery costs at most one 15-minute session sample.
    _schedule_credential_usage_event(
        role=role,
        credential_type=credential_type,
        resource_id=resource_id,
    )


def _schedule_credential_usage_event(
    *,
    role: Role,
    credential_type: CredentialUsageType,
    resource_id: str | uuid.UUID,
) -> None:
    try:
        task = asyncio.create_task(
            _emit_credential_usage_event(
                role=role,
                credential_type=credential_type,
                resource_id=resource_id,
            )
        )
    except RuntimeError as exc:
        logger.warning(
            "Failed to schedule credential usage audit emission",
            credential_type=credential_type,
            error=str(exc),
        )
        return
    _AUDIT_USAGE_TASKS.add(task)
    task.add_done_callback(_handle_usage_task_done)


def _handle_usage_task_done(task: asyncio.Task[None]) -> None:
    _AUDIT_USAGE_TASKS.discard(task)
    if task.cancelled():
        return
    try:
        task.result()
    except Exception as exc:
        logger.warning("Credential usage audit task failed", error=str(exc))


async def _emit_credential_usage_event(
    *,
    role: Role,
    credential_type: CredentialUsageType,
    resource_id: str | uuid.UUID,
) -> None:
    try:
        async with AuditService.with_session(role) as audit_svc:
            await audit_svc.create_event(
                resource_type=credential_type,
                action="use",
                resource_id=resource_id,
                data={"credential_kind": credential_type},
                include_actor_label=False,
            )
    except Exception as exc:
        logger.warning(
            "Credential usage audit emission failed",
            credential_type=credential_type,
            error=str(exc),
        )
