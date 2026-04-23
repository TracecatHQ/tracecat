from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import Workflow
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.schedules.schemas import ScheduleCreate
from tracecat.workflow.schedules.service import WorkflowSchedulesService

pytestmark = pytest.mark.usefixtures("db")


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
