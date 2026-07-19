from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.logger import (
    AuditCallContext,
    AuditEventDetails,
    audit_log,
)
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.models import Webhook
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.identifiers import WorkflowID
from tracecat.webhooks.schemas import WebhookUpdate


async def _webhook_update_audit_details(
    context: AuditCallContext,
) -> AuditEventDetails:
    role = cast(Role, context.arguments["role"])
    session = cast(AsyncSession, context.arguments["session"])
    workflow_id = cast(WorkflowID, context.arguments["workflow_id"])
    params = cast(WebhookUpdate, context.arguments["params"])
    webhook = None
    if role.workspace_id is not None:
        webhook = await get_webhook(
            session,
            workspace_id=role.workspace_id,
            workflow_id=workflow_id,
        )
    return AuditEventDetails(
        resource_id=webhook.id if webhook is not None else None,
        data={
            "changed_fields": sorted(params.model_dump(exclude_unset=True)),
        },
    )


async def get_webhook(
    session: AsyncSession,
    workspace_id,
    workflow_id: WorkflowID,
) -> Webhook | None:
    statement = (
        select(Webhook)
        .where(
            Webhook.workspace_id == workspace_id,
            Webhook.workflow_id == workflow_id,
        )
        .order_by(Webhook.id)
    )
    result = await session.execute(statement)
    return result.scalars().first()


@require_scope("workflow:update")
@audit_log(
    resource_type="webhook",
    action="update",
    resource_id_attr="id",
    attempt_metadata=_webhook_update_audit_details,
)
async def update_webhook(
    *,
    role: Role,
    session: AsyncSession,
    workflow_id: WorkflowID,
    params: WebhookUpdate,
) -> Webhook:
    """Update webhook configuration shared by all control-plane callers."""
    if role.workspace_id is None:
        raise TracecatAuthorizationError("Webhook update requires a workspace")
    webhook = await get_webhook(
        session,
        workspace_id=role.workspace_id,
        workflow_id=workflow_id,
    )
    if webhook is None:
        raise TracecatNotFoundError("Webhook not found")
    for key, value in params.model_dump(exclude_unset=True).items():
        # Safety: params have been validated by WebhookUpdate.
        setattr(webhook, key, value)
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)
    return webhook
