from __future__ import annotations

import uuid

from sqlalchemy import select

from tracecat import config
from tracecat.audit.enums import AuditEventActor, AuditEventStatus
from tracecat.audit.types import AuditEventPayload
from tracecat.audit.worker import enqueue_event
from tracecat.contexts import ctx_client_ip
from tracecat.db.models import AuditEvent, User
from tracecat.service import BaseService


class AuditService(BaseService):
    """Log user-driven events into the in-memory buffer."""

    service_name = "audit"

    async def create_event(
        self,
        *,
        resource_type: str,
        action: str,
        resource_id: uuid.UUID | None = None,
        status: AuditEventStatus = AuditEventStatus.SUCCESS,
    ) -> None:
        role = self.role
        if role is None or role.type != "user" or role.user_id is None:
            self.logger.debug("Skipping audit log", reason="non_user_role", role=role)
            return

        payload = AuditEventPayload(
            organization_id=config.TRACECAT__DEFAULT_ORG_ID,
            workspace_id=role.workspace_id,
            actor_type=AuditEventActor.USER,
            actor_id=role.user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            status=status,
            ip_address=ctx_client_ip.get(),
        )
        await enqueue_event(payload)
        self.logger.debug(
            "Queued audit event",
            actor_id=payload.actor_id,
            resource_type=resource_type,
            action=action,
        )


class AuditPersistService(BaseService):
    """Persist audit events to the primary table."""

    service_name = "audit_persist"

    async def persist(self, events: list[AuditEventPayload]) -> None:
        if not events:
            return
        actor_labels = await self._get_actor_labels(
            [event.actor_id for event in events]
        )
        for payload in events:
            actor_display = actor_labels.get(payload.actor_id)
            self.session.add(
                AuditEvent(
                    organization_id=payload.organization_id,
                    workspace_id=payload.workspace_id,
                    actor_type=payload.actor_type,
                    actor_id=payload.actor_id,
                    actor_display=actor_display,
                    ip_address=payload.ip_address,
                    resource_type=payload.resource_type,
                    resource_id=payload.resource_id,
                    action=payload.action,
                    status=payload.status,
                    created_at=payload.created_at,
                )
            )
        await self.session.commit()

    async def _get_actor_labels(
        self, user_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """Fetch user emails for user-type actors to enrich display values."""
        if not user_ids:
            return {}
        stmt = select(User.__table__.c.id, User.__table__.c.email).where(
            User.__table__.c.id.in_(user_ids)
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        return {row[0]: row[1] for row in rows}
