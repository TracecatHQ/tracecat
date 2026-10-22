from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.exceptions import ApplicationError

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.authz.scopes import EDITOR_SCOPES
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Schedule, Workflow
from tracecat.db.session_events import AfterCommitQueue
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import ScheduleUUID, WorkspaceID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.temporal.errors import extract_error_classification
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.schemas import (
    GetScheduleActivityInputs,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
)
from tracecat.workflow.schedules.service import WorkflowSchedulesService

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "kind", "retry_disposition"),
    [
        (
            TracecatNotFoundError("missing schedule"),
            RuntimeErrorKind.WORKFLOW_BOOTSTRAP_INVALID_DATA,
            RetryDisposition.NON_RETRYABLE,
        ),
        (
            RuntimeError("schedule diagnostic must not enter history"),
            RuntimeErrorKind.WORKFLOW_BOOTSTRAP_UNAVAILABLE,
            RetryDisposition.RETRYABLE,
        ),
    ],
)
async def test_get_schedule_trigger_inputs_activity_classifies_failures(
    svc_role: Role,
    error: Exception,
    kind: RuntimeErrorKind,
    retry_disposition: RetryDisposition,
) -> None:
    mock_service = AsyncMock()
    mock_service.get_schedule.side_effect = error
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_service
    inputs = GetScheduleActivityInputs(
        role=svc_role,
        schedule_id=ScheduleUUID.new_uuid4(),
        workflow_id=WorkflowUUID.new_uuid4(),
    )

    with patch(
        "tracecat.workflow.schedules.service.WorkflowSchedulesService.with_session",
        return_value=mock_ctx,
    ):
        with pytest.raises(ApplicationError) as exc_info:
            await WorkflowSchedulesService.get_schedule_trigger_inputs_activity(inputs)

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is kind
    assert classification.retry_disposition is retry_disposition
    assert str(error) not in str(exc_info.value)


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
async def test_create_schedule_unpublished_workflow_emits_failure(
    session: AsyncSession, svc_role, monkeypatch
):
    workflow = Workflow(
        title="Unpublished Schedule",
        description="Test workflow",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.commit()

    create_event = AsyncMock()
    monkeypatch.setattr(AuditService, "create_event", create_event)
    service = WorkflowSchedulesService(session, role=svc_role)

    with pytest.raises(
        TracecatConflictError,
        match="Workflow must be saved before creating a schedule",
    ):
        await service.create_schedule(
            ScheduleCreate(
                workflow_id=WorkflowUUID.new(workflow.id),
                every=timedelta(hours=1),
                inputs={},
                status="offline",
                timeout=0,
            )
        )

    assert [call.kwargs["status"] for call in create_event.await_args_list] == [
        AuditEventStatus.ATTEMPT,
        AuditEventStatus.FAILURE,
    ]


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
@pytest.mark.parametrize("use_cron", [True, False])
async def test_update_schedule_persists_only_the_active_spec(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    use_cron: bool,
) -> None:
    assert svc_role.workspace_id is not None
    _, schedule = await _create_workflow_with_schedule(session, svc_role.workspace_id)
    if not use_cron:
        schedule.cron = "0 0 * * *"
        schedule.every = None
        await session.commit()
    else:
        schedule.offset = timedelta(minutes=10)
        await session.commit()
    temporal_update = AsyncMock()
    monkeypatch.setattr(bridge, "update_schedule", temporal_update)
    params = (
        ScheduleUpdate(cron="0 9 * * *")
        if use_cron
        else ScheduleUpdate(every=timedelta(hours=2))
    )
    updated = await WorkflowSchedulesService(session, role=svc_role).update_schedule(
        schedule.id, params
    )
    await session.refresh(updated)
    response = ScheduleRead.model_validate(updated)
    assert response.cron == ("0 9 * * *" if use_cron else None)
    assert response.every == (None if use_cron else timedelta(hours=2))
    assert response.offset is None
    temporal_update.assert_awaited_once()
    assert temporal_update.await_args is not None
    sent_params = temporal_update.await_args.args[1]
    assert sent_params.cron == response.cron
    assert sent_params.every == response.every


@pytest.mark.anyio
@pytest.mark.parametrize("use_cron", [True, False])
async def test_update_cannot_remove_the_active_spec(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    use_cron: bool,
) -> None:
    assert svc_role.workspace_id is not None
    _, schedule = await _create_workflow_with_schedule(session, svc_role.workspace_id)
    if use_cron:
        schedule.cron = "0 0 * * *"
        schedule.every = None
        await session.commit()
    temporal_update = AsyncMock()
    monkeypatch.setattr(bridge, "update_schedule", temporal_update)
    queue = AfterCommitQueue.of(session)
    with patch.object(session, "commit", AsyncMock()) as commit:
        with pytest.raises(TracecatValidationError, match="Either cron or every"):
            await WorkflowSchedulesService(session, role=svc_role).update_schedule(
                schedule.id,
                ScheduleUpdate(cron=None) if use_cron else ScheduleUpdate(every=None),
            )
        commit.assert_not_awaited()
    assert queue.callbacks == []
    temporal_update.assert_not_awaited()
    assert schedule.cron == ("0 0 * * *" if use_cron else None)
    assert schedule.every == (None if use_cron else timedelta(hours=1))


@pytest.mark.anyio
@pytest.mark.parametrize("change_every", [True, False])
async def test_update_validates_offset_against_persisted_interval(
    session: AsyncSession, svc_role: Role, change_every: bool
) -> None:
    assert svc_role.workspace_id is not None
    _, schedule = await _create_workflow_with_schedule(session, svc_role.workspace_id)
    schedule.offset = timedelta(minutes=30)
    await session.commit()
    params = (
        ScheduleUpdate(every=timedelta(minutes=15))
        if change_every
        else ScheduleUpdate(offset=timedelta(hours=2))
    )
    with patch.object(session, "commit", AsyncMock()) as commit:
        with pytest.raises(
            TracecatValidationError, match="offset must be less than every"
        ):
            await WorkflowSchedulesService(session, role=svc_role).update_schedule(
                schedule.id, params
            )
        commit.assert_not_awaited()
    assert AfterCommitQueue.of(session).callbacks == []


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_schedule_deleted_while_waiting_for_lock_is_not_found(
    svc_role: Role, operation: str
) -> None:
    assert svc_role.workspace_id is not None
    async with (
        get_async_session_context_manager() as session,
        get_async_session_context_manager() as contender,
        get_async_session_context_manager() as observer,
    ):
        workflow, schedule = await _create_workflow_with_schedule(
            session, svc_role.workspace_id
        )
        schedule_id = schedule.id
        workflow_id = workflow.id
        await WorkflowSchedulesService(
            session, role=svc_role
        )._get_schedule_with_workflow_lock(schedule_id)
        pid = await contender.scalar(text("SELECT pg_backend_pid()"))
        service = WorkflowSchedulesService(contender, role=svc_role)

        async def mutate_schedule() -> None:
            if operation == "update":
                await service.update_schedule(
                    schedule_id, ScheduleUpdate(status="online")
                )
            else:
                await service.delete_schedule(schedule_id)

        task = asyncio.create_task(mutate_schedule())
        try:
            async with asyncio.timeout(5):
                while not await observer.scalar(
                    text(
                        "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
                    ),
                    {"pid": pid},
                ):
                    await asyncio.sleep(0.01)
            await session.delete(schedule)
            await session.commit()
            with pytest.raises(TracecatNotFoundError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await contender.rollback()
            await session.rollback()
            await session.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await session.commit()


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
