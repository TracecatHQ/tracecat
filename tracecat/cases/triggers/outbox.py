"""Write case trigger outbox rows in the case transaction."""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.cases.enums import CaseEventType
from tracecat.cases.triggers.types import CaseCommentWorkflowTrigger
from tracecat.db.models import CaseEvent, CaseTrigger, CaseTriggerOutbox


def matching_trigger_event_types(event_type: CaseEventType) -> tuple[str, ...]:
    """Return the trigger subscriptions that an event type fires.

    Cases emit ``case_closed`` / ``case_reopened`` instead of
    ``status_changed`` for those transitions, so triggers subscribed to
    ``status_changed`` must match them too.
    """
    if event_type in (CaseEventType.CASE_CLOSED, CaseEventType.CASE_REOPENED):
        return (event_type.value, CaseEventType.STATUS_CHANGED.value)
    return (event_type.value,)


def online_trigger_filter(
    workspace_id: uuid.UUID, event_type: CaseEventType
) -> ColumnElement[bool]:
    """Filter online case triggers in a workspace that an event type fires."""
    return (
        (CaseTrigger.workspace_id == workspace_id)
        & (CaseTrigger.status == "online")
        & or_(
            *[
                CaseTrigger.event_types.contains([matching_type])
                for matching_type in matching_trigger_event_types(event_type)
            ]
        )
    )


def is_workflow_originated(event: CaseEvent) -> bool:
    """Whether a workflow run made this case change.

    Case triggers do not fire on these events, so a workflow that updates a
    case cannot trigger itself in a loop.
    """
    return isinstance(event.data, dict) and bool(event.data.get("wf_exec_id"))


async def has_online_trigger(
    session: AsyncSession, workspace_id: uuid.UUID, event_type: CaseEventType
) -> bool:
    """Whether the workspace has an online trigger that the event type fires."""
    return bool(
        await session.scalar(
            select(exists().where(online_trigger_filter(workspace_id, event_type)))
        )
    )


async def enqueue_case_trigger(
    session: AsyncSession,
    *,
    event: CaseEvent,
    comment_workflow: CaseCommentWorkflowTrigger | None = None,
) -> None:
    """Add an outbox row for a case event when it may start a workflow.

    The row commits or rolls back with the case change. A comment that picks a
    workflow always gets a row; any other event gets one only when the
    workspace has an online trigger for its event type.
    """
    if comment_workflow is None:
        if not config.TRACECAT__CASE_TRIGGERS_ENABLED:
            return
        if is_workflow_originated(event):
            return
        if not await has_online_trigger(
            session, event.workspace_id, CaseEventType(event.type)
        ):
            return

    session.add(
        CaseTriggerOutbox(
            workspace_id=event.workspace_id,
            case_id=event.case_id,
            event_id=event.id,
            extras=comment_workflow.model_dump(mode="json")
            if comment_workflow is not None
            else None,
        )
    )
