from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.scopes import EDITOR_SCOPES
from tracecat.db.models import Schedule, Workflow
from tracecat.exceptions import ScopeDeniedError
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


def _editor_role(svc_role) -> Role:
    """A workspace-editor role: workflow:update + schedule:create, no schedule:delete."""
    return svc_role.model_copy(update={"scopes": EDITOR_SCOPES})


@pytest.mark.anyio
async def test_replace_schedules_allowed_for_workflow_editor(
    session: AsyncSession, svc_role, monkeypatch
):
    """Editing schedules through the workflow-edit surface is gated by
    workflow:update, so an editor without the admin-only schedule:delete scope
    can replace schedules (regression for the edit_workflow RBAC failure)."""
    workflow, schedule = await _create_workflow_with_schedule(
        session, svc_role.workspace_id
    )

    async def _delete_schedule(schedule_id):
        return None

    async def _create_schedule(*, schedule_id, **kwargs):
        return SimpleNamespace(id=str(schedule_id))

    monkeypatch.setattr(bridge, "delete_schedule", _delete_schedule)
    monkeypatch.setattr(bridge, "create_schedule", _create_schedule)

    editor_role = _editor_role(svc_role)
    assert "schedule:delete" not in (editor_role.scopes or frozenset())
    service = WorkflowSchedulesService(session, role=editor_role)

    await service.replace_schedules(
        WorkflowUUID.new(workflow.id),
        [
            ScheduleCreate(
                workflow_id=WorkflowUUID.new(workflow.id),
                every=timedelta(hours=2),
                inputs={},
                status="offline",
                timeout=0,
            )
        ],
    )

    remaining = await service.list_schedules(workflow_id=WorkflowUUID.new(workflow.id))
    assert len(remaining) == 1
    # The pre-existing schedule was deleted and the new one created.
    assert remaining[0].id != schedule.id
    assert remaining[0].every == timedelta(hours=2)


@pytest.mark.anyio
async def test_delete_schedule_still_requires_admin_scope(
    session: AsyncSession, svc_role
):
    """The standalone delete_schedule surface keeps its schedule:delete gate;
    the workflow-edit replacement path must not have weakened it."""
    _, schedule = await _create_workflow_with_schedule(session, svc_role.workspace_id)
    service = WorkflowSchedulesService(session, role=_editor_role(svc_role))

    with pytest.raises(ScopeDeniedError):
        await service.delete_schedule(schedule.id)
