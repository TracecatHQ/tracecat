from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.logger import (
    AuditEventDetails,
    audit_log,
)
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.models import Webhook, Workflow
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.identifiers import WorkflowID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.managed_resources.guard import (
    ensure_unmanaged,
    hold_for_customer_write,
)
from tracecat.webhooks.schemas import WebhookCreate, WebhookUpdate
from tracecat.workspace_sync.enums import SyncResourceType


async def _webhook_update_audit_details(
    *,
    role: Role,
    session: AsyncSession,
    workflow_id: WorkflowID,
    params: WebhookUpdate,
) -> AuditEventDetails:
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
            "changed_fields": sorted(params.model_fields_set),
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
    action="create",
    resource_id_attr="id",
)
async def create_webhook(
    *,
    role: Role,
    session: AsyncSession,
    workflow_id: WorkflowID,
    params: WebhookCreate,
) -> Webhook:
    """Create a webhook for a workflow."""
    if role.workspace_id is None:
        raise TracecatAuthorizationError("Webhook creation requires a workspace")
    # Locking the workflow serializes concurrent creates for the existence check.
    workflow = await session.scalar(
        select(Workflow.id)
        .where(Workflow.workspace_id == role.workspace_id, Workflow.id == workflow_id)
        .with_for_update()
    )
    if workflow is None:
        raise TracecatNotFoundError("Workflow not found")
    if await get_webhook(session, role.workspace_id, workflow_id) is not None:
        raise TracecatConflictError(
            "Workflow already has a webhook. Update it with PATCH instead."
        )
    webhook = Webhook(
        workspace_id=role.workspace_id,
        methods=cast(list[str], params.methods),
        workflow_id=workflow_id,
        status=params.status,
        allowlisted_cidrs=params.allowlisted_cidrs,
        include_headers=params.include_headers,
    )
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)
    return webhook


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
    # Customers may toggle a managed workflow's webhook on and off; its other
    # settings come from the package. A toggle still takes the ownership lock,
    # so it cannot land while a rollout carries the old status forward.
    if params.model_fields_set - {"status"}:
        await ensure_unmanaged(
            session,
            workspace_id=role.workspace_id,
            resource_type=SyncResourceType.WORKFLOW,
            local_id=WorkflowUUID.new(workflow_id),
        )
    else:
        await hold_for_customer_write(session, role.workspace_id)
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
