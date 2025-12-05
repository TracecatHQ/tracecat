from __future__ import annotations

from temporalio import activity

from tracecat.audit.service import AuditPersistService
from tracecat.audit.types import AuditEventPayload


@activity.defn(name="audit_persist_activity")
async def audit_persist_activity(events: list[dict]) -> int:
    payloads = [AuditEventPayload.model_validate(event) for event in events]
    async with AuditPersistService.with_session() as svc:
        await svc.persist(payloads)
    return len(events)
