"""Replay external side effects from the database import receipt."""

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import ScheduleAlreadyRunningError
from temporalio.service import RPCError, RPCStatusCode

from tracecat.auth.types import Role
from tracecat.cases.durations.reconciliation import CaseDurationBackfills
from tracecat.cases.durations.sync_queue import publish_case_duration_sync
from tracecat.db.models import Schedule, WorkspaceSyncOperation
from tracecat.dsl.client import get_temporal_client
from tracecat.identifiers import ScheduleUUID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.reconciliation import ScheduleChanges
from tracecat.workflow.schedules.schemas import ScheduleUpdate

SCHEDULE_CHANGES = TypeAdapter(ScheduleChanges)
CASE_DURATION_BACKFILLS = TypeAdapter(CaseDurationBackfills)


async def reconcile_case_duration_backfills(operation: WorkspaceSyncOperation) -> None:
    """Publish committed backfills before completion; retries may safely republish."""
    backfills = CASE_DURATION_BACKFILLS.validate_python(
        (operation.summary or {}).get("case_duration_backfills", {})
    )
    for workspace_id in backfills.workspaces:
        # Redis errors must reach the activity retry, never a lossy fallback.
        # The consumer recalculates from current definitions, so duplicate
        # messages after a lost acknowledgement cannot restore stale values.
        await publish_case_duration_sync(
            workspace_id=workspace_id, reason="duration_definition_updated"
        )


async def reconcile_schedules(
    session: AsyncSession, role: Role, operation: WorkspaceSyncOperation
) -> None:
    """Finish a committed import; repeated calls never create new schedule IDs."""
    changes = SCHEDULE_CHANGES.validate_python(
        (operation.summary or {}).get("schedule_changes", {})
    )
    if not changes.created and not changes.deleted:
        return
    client = await get_temporal_client()
    for schedule_id in changes.deleted:
        try:
            await client.get_schedule_handle(
                ScheduleUUID.new(schedule_id).to_legacy()
            ).delete()
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
    # Retain permissions authorized at the import commit, including on replay
    # after the initiating identity loses access. Never add service grants.
    schedule_role = Role.model_validate(
        (operation.summary or {}).get("schedule_role", role.model_dump())
    )
    service_role = schedule_role.model_copy(
        update={
            "type": "service",
            "service_id": "tracecat-schedule-runner",
            "user_id": None,
        }
    )
    for schedule_id in changes.created:
        # Read the current desired row under a lock: edits since the import must
        # win, and a deleted schedule must never be resurrected by a retry.
        schedule = await session.scalar(
            select(Schedule)
            .where(
                Schedule.id == schedule_id,
                Schedule.workspace_id == role.workspace_id,
            )
            .with_for_update()
        )
        if schedule is None:
            continue
        try:
            await bridge.create_schedule(
                workflow_id=WorkflowUUID.new(schedule.workflow_id),
                schedule_id=schedule.id,
                role=service_role,
                cron=schedule.cron,
                every=schedule.every,
                offset=schedule.offset,
                start_at=schedule.start_at,
                end_at=schedule.end_at,
                timeout=schedule.timeout,
                status=schedule.status,
            )
        except ScheduleAlreadyRunningError:
            # The preceding attempt may have created it and lost the response.
            # Apply the current row in case a subsequent edit's callback failed.
            await bridge.update_schedule(
                schedule.id,
                ScheduleUpdate(
                    cron=schedule.cron,
                    every=schedule.every,
                    offset=schedule.offset,
                    start_at=schedule.start_at,
                    end_at=schedule.end_at,
                    timeout=schedule.timeout,
                    status=schedule.status,
                ),
            )
