"""Webhook configuration writes.

A webhook that has been turned on is also a ``webhook`` trigger: a
``trigger`` row with one workflow binding, linked by ``webhook.trigger_id``,
so it shows in the Triggers tab. Every write here that turns a webhook on
gives it that row if it has none, in the same transaction; it keeps the row
when it goes offline. A database trigger on ``webhook`` deletes the row with
the webhook and copies every status change to it.

Writes lock the webhook row and then (through the binding's foreign key)
its workflow: the order in which a workflow delete removes them (the ORM
deletes the webhook before the workflow, in this release and the previous
one) and the migration's backfill locks them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.logger import (
    AuditEventDetails,
    audit_log,
)
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.models import Trigger, TriggerBinding, Webhook, Workflow
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
from tracecat.triggers.enums import TriggerKind, TriggerStatus, TriggerTargetType
from tracecat.webhooks.schemas import WebhookCreate, WebhookUpdate
from tracecat.workspace_sync.enums import SyncResourceType

# The trigger name column is 255 characters; leave room for the suffix.
_TITLE_IN_NAME_MAX = 255 - len(" webhook")


def default_trigger_name(workflow_title: str | None) -> str:
    """The name a webhook's trigger gets when none is given.

    The workflow title plus " webhook", or "Webhook" for an untitled
    workflow. The migration backfill uses the same rule.
    """
    title = (workflow_title or "").strip()
    if not title:
        return "Webhook"
    return f"{title[:_TITLE_IN_NAME_MAX]} webhook"


@dataclass(frozen=True, slots=True)
class _LockedWebhook:
    workflow_title: str
    webhook: Webhook


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


async def lock_webhook(
    session: AsyncSession, workspace_id: uuid.UUID, workflow_id: uuid.UUID
) -> tuple[str | None, Webhook | None]:
    """Lock a workflow's webhook until commit, and read the workflow's title.

    The webhook is read fresh, so a check against it (such as whether it has
    a trigger row yet) sees what a concurrent writer committed. The workflow
    row is not locked: a binding insert takes its key-share lock later, after
    the webhook's, the same order a workflow delete takes them.

    Returns:
        The workflow title (``None`` if there is no such workflow) and the
        webhook (``None`` if there is no such workflow or it has no webhook).
    """
    webhook = await session.scalar(
        select(Webhook)
        .where(Webhook.workspace_id == workspace_id, Webhook.workflow_id == workflow_id)
        .order_by(Webhook.id)
        .limit(1)
        .with_for_update(of=Webhook)
        .execution_options(populate_existing=True)
    )
    # The title column is NOT NULL, so None means no such workflow.
    title = await session.scalar(
        select(Workflow.title).where(
            Workflow.workspace_id == workspace_id, Workflow.id == workflow_id
        )
    )
    if title is None:
        return None, None
    return title, webhook


async def add_webhook_trigger(
    session: AsyncSession, webhook: Webhook, *, name: str
) -> uuid.UUID:
    """Give a webhook its ``webhook`` trigger row and its workflow binding.

    The binding is the webhook's workflow, the only target a webhook runs
    until webhooks start their targets through bindings. It is online even
    for an offline webhook, as for schedules: the trigger's status is what
    counts until bindings drive the runtime (step 2 must revisit this). The
    caller holds the webhook's lock (``lock_webhook``) and commits.
    """
    trigger = Trigger(
        id=uuid.uuid4(),
        workspace_id=webhook.workspace_id,
        kind=TriggerKind.WEBHOOK.value,
        name=name,
        status=webhook.status,
    )
    session.add(trigger)
    # The binding's and the webhook's foreign keys need the row first.
    await session.flush()
    session.add(
        TriggerBinding(
            workspace_id=webhook.workspace_id,
            trigger_id=trigger.id,
            target_type=TriggerTargetType.WORKFLOW.value,
            workflow_id=webhook.workflow_id,
            status=TriggerStatus.ONLINE.value,
        )
    )
    webhook.trigger_id = trigger.id
    session.add(webhook)
    return trigger.id


async def _write_webhook(
    session: AsyncSession,
    locked: _LockedWebhook,
    params: WebhookUpdate,
    *,
    trigger_name: str | None = None,
    as_trigger: bool = False,
) -> None:
    """Apply ``params`` and keep the webhook's trigger row in step.

    A webhook that is online after the update (or ``as_trigger``, a create
    from the Triggers tab) gets a trigger row if it has none. One that has a
    row gets its status, name and timestamp written with it.
    """
    webhook = locked.webhook
    for key, value in params.model_dump(exclude_unset=True).items():
        # Safety: params have been validated by WebhookUpdate.
        setattr(webhook, key, value)
    session.add(webhook)
    if webhook.trigger_id is None:
        if as_trigger or webhook.status == TriggerStatus.ONLINE:
            await add_webhook_trigger(
                session,
                webhook,
                name=trigger_name or default_trigger_name(locked.workflow_title),
            )
        return
    # The header's timestamp covers its settings too. The database trigger
    # also copies the status; writing it here keeps the loaded row current.
    values: dict[str, Any] = {"updated_at": func.now()}
    if "status" in params.model_fields_set:
        values["status"] = webhook.status
    if trigger_name is not None:
        values["name"] = trigger_name
    await session.execute(
        update(Trigger)
        .where(
            Trigger.workspace_id == webhook.workspace_id,
            Trigger.id == webhook.trigger_id,
        )
        .values(**values)
    )


async def _guard_write(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    workflow_id: WorkflowID,
    params: WebhookUpdate,
) -> None:
    # Customers may toggle a managed workflow's webhook on and off; its other
    # settings come from the package. A toggle still takes the ownership lock,
    # so it cannot land while a rollout carries the old status forward.
    if params.model_fields_set - {"status"}:
        await ensure_unmanaged(
            session,
            workspace_id=workspace_id,
            resource_type=SyncResourceType.WORKFLOW,
            local_id=WorkflowUUID.new(workflow_id),
        )
    else:
        await hold_for_customer_write(session, workspace_id)


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
    # The title column is NOT NULL, so None means no such workflow.
    title = await session.scalar(
        select(Workflow.title)
        .where(Workflow.workspace_id == role.workspace_id, Workflow.id == workflow_id)
        .with_for_update()
    )
    if title is None:
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
    if webhook.status == TriggerStatus.ONLINE:
        await add_webhook_trigger(session, webhook, name=default_trigger_name(title))
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
    """Update webhook configuration shared by all control-plane callers.

    Turning the webhook on gives it its trigger row if it has none.
    """
    if role.workspace_id is None:
        raise TracecatAuthorizationError("Webhook update requires a workspace")
    await _guard_write(session, role.workspace_id, workflow_id, params)
    title, webhook = await lock_webhook(
        session, role.workspace_id, WorkflowUUID.new(workflow_id)
    )
    if title is None or webhook is None:
        raise TracecatNotFoundError("Webhook not found")
    await _write_webhook(session, _LockedWebhook(title, webhook), params)
    await session.commit()
    await session.refresh(webhook)
    return webhook


# ----- Entry points for the Triggers tab -----
# Each writes the webhook and its trigger row and binding, and commits, so its
# ``webhook`` audit record follows the commit as on the workflow page. Each
# needs ``workflow:update``, as the workflow page's webhook panel does: the
# Triggers tab is another way in, not a way around it.


async def _trigger_webhook_workflow(
    session: AsyncSession, workspace_id: uuid.UUID, trigger_id: uuid.UUID
) -> WorkflowUUID:
    """The workflow of the webhook behind a trigger.

    Raises:
        TracecatNotFoundError: If no webhook belongs to the trigger.
    """
    workflow_id = await session.scalar(
        select(Webhook.workflow_id).where(
            Webhook.workspace_id == workspace_id, Webhook.trigger_id == trigger_id
        )
    )
    if workflow_id is None:
        raise TracecatNotFoundError(f"No webhook for trigger {trigger_id}")
    return WorkflowUUID.new(workflow_id)


async def _lock_trigger_webhook(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    workflow_id: uuid.UUID,
    trigger_id: uuid.UUID,
) -> _LockedWebhook:
    title, webhook = await lock_webhook(session, workspace_id, workflow_id)
    # Read again under the lock: the trigger may have been deleted meanwhile.
    if title is None or webhook is None or webhook.trigger_id != trigger_id:
        raise TracecatNotFoundError(f"No webhook for trigger {trigger_id}")
    return _LockedWebhook(title, webhook)


def _changed(webhook: Webhook, params: WebhookUpdate) -> WebhookUpdate:
    """Only the fields of ``params`` that differ from the webhook's.

    A Triggers tab config carries every setting; keeping only the changes
    lets a managed workflow's webhook be turned on and off from the tab, as
    on the workflow page, while its other settings stay the package's.
    """
    current = {
        "status": webhook.status,
        "methods": sorted(webhook.methods or []),
        "allowlisted_cidrs": webhook.allowlisted_cidrs or [],
        "include_headers": bool(webhook.include_headers),
    }
    changed: dict[str, Any] = {}
    for key, value in params.model_dump(exclude_unset=True).items():
        compared = sorted(value) if key == "methods" else value
        if compared != current.get(key):
            changed[key] = value
    return WebhookUpdate.model_validate(changed)


def _changed_fields(
    params: WebhookUpdate, trigger_name: str | None
) -> AuditEventDetails:
    changed = set(params.model_fields_set)
    if trigger_name is not None:
        changed.add("name")
    return AuditEventDetails(data={"changed_fields": sorted(changed)})


def _create_trigger_audit_details(
    *,
    role: Role,
    session: AsyncSession,
    workflow_id: WorkflowID,
    params: WebhookUpdate,
    trigger_name: str,
) -> AuditEventDetails:
    return _changed_fields(params, trigger_name)


def _update_trigger_audit_details(
    *,
    role: Role,
    session: AsyncSession,
    trigger_id: uuid.UUID,
    params: WebhookUpdate,
    trigger_name: str | None = None,
) -> AuditEventDetails:
    return _changed_fields(params, trigger_name)


@require_scope("workflow:update")
@audit_log(
    resource_type="webhook",
    action="update",
    resource_id_attr="id",
    attempt_metadata=_create_trigger_audit_details,
)
async def create_trigger_webhook(
    *,
    role: Role,
    session: AsyncSession,
    workflow_id: WorkflowID,
    params: WebhookUpdate,
    trigger_name: str,
) -> Webhook:
    """Make a workflow's webhook a trigger, with the given settings and status.

    The webhook row already exists (every workflow has one); this applies
    ``params`` to it and adds its trigger row and binding.

    Raises:
        TracecatNotFoundError: If the workflow or its webhook is not in the
            workspace.
        TracecatConflictError: If the webhook already is a trigger.
    """
    if role.workspace_id is None:
        raise TracecatAuthorizationError("Webhook update requires a workspace")
    workflow_uuid = WorkflowUUID.new(workflow_id)
    # Ownership lock first, so the row compared below is the one written:
    # no rollout can change it between the managed check and the write.
    await hold_for_customer_write(session, role.workspace_id)
    title, webhook = await lock_webhook(session, role.workspace_id, workflow_uuid)
    if title is None or webhook is None:
        raise TracecatNotFoundError("Webhook not found")
    if webhook.trigger_id is not None:
        raise TracecatConflictError(
            "This workflow's webhook is already a trigger. Open it in the"
            " Triggers tab to change it."
        )
    changes = _changed(webhook, params)
    await _guard_write(session, role.workspace_id, workflow_uuid, changes)
    await _write_webhook(
        session,
        _LockedWebhook(title, webhook),
        changes,
        trigger_name=trigger_name,
        as_trigger=True,
    )
    await session.commit()
    await session.refresh(webhook)
    return webhook


@require_scope("workflow:update")
@audit_log(
    resource_type="webhook",
    action="update",
    resource_id_attr="id",
    attempt_metadata=_update_trigger_audit_details,
)
async def update_trigger_webhook(
    *,
    role: Role,
    session: AsyncSession,
    trigger_id: uuid.UUID,
    params: WebhookUpdate,
    trigger_name: str | None = None,
) -> Webhook:
    """Update the webhook behind a ``webhook`` trigger, and its trigger row.

    Raises:
        TracecatNotFoundError: If no webhook belongs to the trigger.
    """
    if role.workspace_id is None:
        raise TracecatAuthorizationError("Webhook update requires a workspace")
    workflow_id = await _trigger_webhook_workflow(
        session, role.workspace_id, trigger_id
    )
    # Ownership lock first, so the row compared below is the one written:
    # no rollout can change it between the managed check and the write.
    await hold_for_customer_write(session, role.workspace_id)
    locked = await _lock_trigger_webhook(
        session, role.workspace_id, workflow_id, trigger_id
    )
    changes = _changed(locked.webhook, params)
    await _guard_write(session, role.workspace_id, workflow_id, changes)
    await _write_webhook(session, locked, changes, trigger_name=trigger_name)
    await session.commit()
    await session.refresh(locked.webhook)
    return locked.webhook


@require_scope("workflow:update")
@audit_log(resource_type="webhook", action="update", resource_id_attr="id")
async def delete_trigger_webhook(
    *, role: Role, session: AsyncSession, trigger_id: uuid.UUID
) -> Webhook:
    """Turn the webhook behind a trigger offline and delete the trigger row.

    The webhook row stays (every workflow has one), with its settings and
    URL; it gets a new trigger row when it is next turned on.

    Raises:
        TracecatNotFoundError: If no webhook belongs to the trigger.
    """
    if role.workspace_id is None:
        raise TracecatAuthorizationError("Webhook update requires a workspace")
    workflow_id = await _trigger_webhook_workflow(
        session, role.workspace_id, trigger_id
    )
    # Only the status changes, as for an on/off toggle on the workflow page.
    await hold_for_customer_write(session, role.workspace_id)
    locked = await _lock_trigger_webhook(
        session, role.workspace_id, workflow_id, trigger_id
    )
    locked.webhook.status = TriggerStatus.OFFLINE.value
    session.add(locked.webhook)
    await session.flush()
    # The key nulls webhook.trigger_id; the binding goes by cascade.
    await session.execute(
        delete(Trigger).where(
            Trigger.workspace_id == role.workspace_id, Trigger.id == trigger_id
        )
    )
    await session.commit()
    await session.refresh(locked.webhook)
    return locked.webhook
