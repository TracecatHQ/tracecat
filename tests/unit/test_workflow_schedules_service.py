from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import Schedule, Workflow
from tracecat.identifiers import WorkspaceID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.schemas import ScheduleCreate, ScheduleUpdate
from tracecat.workflow.schedules.service import WorkflowSchedulesService

pytestmark = pytest.mark.usefixtures("db")


async def _create_workflow_with_schedule(
    session: AsyncSession, workspace_id: WorkspaceID
) -> tuple[Workflow, Schedule]:
    workflow = Workflow(
        title="Schedule Lock",
        description="Test workflow",
        status="offline",
        workspace_id=workspace_id,
    )
    session.add(workflow)
    await session.flush()

    schedule = Schedule(
        workspace_id=workspace_id,
        workflow_id=workflow.id,
        every=timedelta(hours=1),
        inputs={},
        status="offline",
        timeout=0,
    )
    session.add(schedule)
    await session.commit()
    return workflow, schedule


@pytest.mark.anyio
async def test_create_schedule_acquires_workflow_lock(
    session: AsyncSession, svc_role, monkeypatch
):
    workflow = Workflow(
        title="Schedule Lock",
        description="Test workflow",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.commit()

    service = WorkflowSchedulesService(session, role=svc_role)
    locked_workflow_ids: list[WorkflowUUID] = []

    async def _lock_workflow(workflow_id: WorkflowUUID) -> None:
        locked_workflow_ids.append(WorkflowUUID.new(workflow_id))

    monkeypatch.setattr(service, "_lock_workflow", _lock_workflow)
    schedule = await service.create_schedule(
        ScheduleCreate(
            workflow_id=WorkflowUUID.new(workflow.id),
            every=timedelta(hours=1),
            inputs={},
            status="offline",
            timeout=0,
        ),
        commit=False,
    )

    assert schedule.workflow_id == workflow.id
    assert schedule.status == "offline"
    assert locked_workflow_ids == [WorkflowUUID.new(workflow.id)]


@pytest.mark.anyio
async def test_update_schedule_updates_existing_schedule(
    session: AsyncSession, svc_role, monkeypatch
):
    _, schedule = await _create_workflow_with_schedule(session, svc_role.workspace_id)
    updated_temporal_schedule_ids = []

    async def _update_schedule(schedule_id, params):
        updated_temporal_schedule_ids.append(schedule_id)
        assert params.status == "online"

    monkeypatch.setattr(bridge, "update_schedule", _update_schedule)

    service = WorkflowSchedulesService(session, role=svc_role)
    updated = await service.update_schedule(
        schedule.id,
        ScheduleUpdate(status="online"),
    )

    assert updated.status == "online"
    assert updated_temporal_schedule_ids == [schedule.id]


@pytest.mark.anyio
async def test_delete_schedule_deletes_existing_schedule(
    session: AsyncSession, svc_role, monkeypatch
):
    _, schedule = await _create_workflow_with_schedule(session, svc_role.workspace_id)
    deleted_temporal_schedule_ids = []

    async def _delete_schedule(schedule_id):
        deleted_temporal_schedule_ids.append(schedule_id)

    monkeypatch.setattr(bridge, "delete_schedule", _delete_schedule)

    service = WorkflowSchedulesService(session, role=svc_role)
    await service.delete_schedule(schedule.id)

    result = await session.execute(select(Schedule).where(Schedule.id == schedule.id))
    assert result.scalar_one_or_none() is None
    assert deleted_temporal_schedule_ids == [schedule.id]
