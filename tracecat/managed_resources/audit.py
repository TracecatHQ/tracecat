"""Audit events for managed resources, delivered to two places.

Tracecat staff change resources inside customer organizations, so each
event goes to the platform audit sink with the staff member as actor, and,
for rollouts and unassigns, to the target organization's own sink. In the
customer's log the actor is "Tracecat", never the staff member.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.audit.types import AuditAction, AuditMetadata, AuditResourceType
from tracecat.auth.types import PlatformRole, Role
from tracecat.logger import logger

TRACECAT_ACTOR_ID = uuid.UUID("7a5c0000-0000-4000-8000-000000000001")
"""Stable actor id standing for Tracecat staff in customer audit logs."""

TRACECAT_ACTOR_LABEL = "Tracecat"


async def audit_managed_event(
    session: AsyncSession,
    *,
    actor: PlatformRole | Role | None,
    resource_type: AuditResourceType,
    action: AuditAction,
    resource_id: uuid.UUID | None = None,
    status: AuditEventStatus = AuditEventStatus.SUCCESS,
    data: AuditMetadata | None = None,
    target_organization_id: uuid.UUID | None = None,
    target_workspace_id: uuid.UUID | None = None,
) -> None:
    """Deliver a managed resources audit event; never fail the caller.

    Args:
        actor: Who acted. Platform events skip when ``None``.
        target_organization_id: Also deliver to this organization's sink,
            with "Tracecat" as the actor.
    """
    try:
        if actor is not None:
            await AuditService(session, role=actor, audit_sink="platform").create_event(
                resource_type=resource_type,
                action=action,
                resource_id=resource_id,
                status=status,
                data=data,
            )
        if target_organization_id is not None:
            customer_role = Role(
                type="service",
                service_id="tracecat-service",
                organization_id=target_organization_id,
                workspace_id=target_workspace_id,
                user_id=TRACECAT_ACTOR_ID,
            )
            await AuditService(
                session, role=customer_role, audit_sink="organization"
            ).create_event(
                resource_type=resource_type,
                action=action,
                resource_id=resource_id,
                status=status,
                data=data,
                actor_label=TRACECAT_ACTOR_LABEL,
                include_ip_address=False,
                include_user_agent=False,
            )
    except Exception as e:
        logger.opt(exception=e).warning(
            "Could not deliver managed resources audit event",
            resource_type=resource_type,
            action=action,
        )
