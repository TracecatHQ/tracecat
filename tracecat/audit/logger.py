from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.contexts import ctx_session


class AuditLogContext:
    """Mutable handle returned by :func:`AuditLogger` to enrich audit events."""

    __slots__ = ("resource_type", "action", "resource_id")

    def __init__(
        self,
        *,
        resource_type: str,
        action: str,
        resource_id: uuid.UUID | None = None,
    ) -> None:
        self.resource_type = resource_type
        self.action = action
        self.resource_id = resource_id

    def set_resource(self, resource_id: uuid.UUID) -> None:
        """Attach the canonical resource identifier once it is available."""

        self.resource_id = resource_id


@asynccontextmanager
async def AuditLogger(
    *,
    resource_type: str,
    action: str,
    resource_id: uuid.UUID | None = None,
    session: AsyncSession | None = None,
) -> AsyncIterator[AuditLogContext]:
    """Context manager for logging audit events with attempt/success/failure status.

    Usage:
        async with AuditLogger(
            resource_type="workflow",
            action="create",
            resource_id=workflow_id,
        ) as audit:
            result = await create_case(...)
            audit.set_resource(result.id)

    This will automatically log:
    - ATTEMPT before the action
    - SUCCESS if the action completes without exception
    - FAILURE if an exception is raised

    Args:
        resource_type: The type of resource being acted upon (e.g., "workflow", "case").
        action: The action being performed (e.g., "create", "update", "delete").
        resource_id: Optional identifier for the resource.
        session: Optional database session. If not provided, uses ctx_session.
    """
    # Get session from context if not provided
    if session is None:
        session = ctx_session.get()
        if session is None:
            raise ValueError(
                "No session provided and ctx_session is not set. "
                "Either provide a session or ensure ctx_session is set."
            )

    audit_service = AuditService(session)
    context = AuditLogContext(
        resource_type=resource_type,
        action=action,
        resource_id=resource_id,
    )

    # Log attempt
    await audit_service.create_event(
        resource_type=resource_type,
        action=action,
        resource_id=context.resource_id,
        status=AuditEventStatus.ATTEMPT,
    )

    try:
        yield context
        # Log success if no exception was raised
        await audit_service.create_event(
            resource_type=resource_type,
            action=action,
            resource_id=context.resource_id,
            status=AuditEventStatus.SUCCESS,
        )
    except Exception:
        # Log failure on exception
        await audit_service.create_event(
            resource_type=resource_type,
            action=action,
            resource_id=context.resource_id,
            status=AuditEventStatus.FAILURE,
        )
        # Re-raise the exception
        raise
