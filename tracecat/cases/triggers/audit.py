"""Audit events for workflows a user picks when posting a case comment."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role


async def audit_comment_workflow_execution(
    session: AsyncSession,
    role: Role,
    *,
    status: AuditEventStatus,
    workflow_id: uuid.UUID,
    case_id: uuid.UUID,
    comment_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    wf_exec_id: str,
) -> None:
    """Record an attempt, start or failure of a comment's picked workflow."""
    async with AuditService.with_session(role=role, session=session) as svc:
        await svc.create_event(
            resource_type="workflow_execution",
            action="create",
            resource_id=workflow_id,
            status=status,
            data={
                "case_id": str(case_id),
                "comment_id": str(comment_id),
                "parent_id": str(parent_id) if parent_id is not None else None,
                "workflow_id": str(workflow_id),
                "wf_exec_id": wf_exec_id,
                "trigger_type": "case",
            },
        )
