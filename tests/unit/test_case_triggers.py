import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.models import CaseTrigger, Workflow
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.case_triggers.schemas import CaseTriggerConfig, CaseTriggerUpdate
from tracecat.workflow.case_triggers.service import CaseTriggersService

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_case_trigger_config_requires_event_types_when_online():
    with pytest.raises(ValidationError):
        CaseTriggerConfig(status="online", event_types=[], tag_filters=[])


@pytest.mark.anyio
async def test_case_trigger_update_requires_events_when_online(
    session: AsyncSession, svc_role
):
    workflow = Workflow(
        title="Case Trigger Test",
        description="Test workflow",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.flush()

    case_trigger = CaseTrigger(
        workspace_id=svc_role.workspace_id,
        workflow_id=workflow.id,
        status="offline",
        event_types=[],
        tag_filters=[],
    )
    session.add(case_trigger)
    await session.commit()

    service = CaseTriggersService(session, role=svc_role)
    with pytest.raises(TracecatValidationError):
        await service.update_case_trigger(
            WorkflowUUID.new(workflow.id), CaseTriggerUpdate(status="online")
        )


@pytest.mark.anyio
async def test_case_trigger_create_missing_tags(session: AsyncSession, svc_role):
    workflow = Workflow(
        title="Case Trigger Tags",
        description="Test workflow",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.flush()

    case_trigger = CaseTrigger(
        workspace_id=svc_role.workspace_id,
        workflow_id=workflow.id,
        status="offline",
        event_types=[],
        tag_filters=[],
    )
    session.add(case_trigger)
    await session.commit()

    service = CaseTriggersService(session, role=svc_role)
    config = CaseTriggerConfig(
        status="offline",
        event_types=[],
        tag_filters=["phishing"],
    )
    await service.upsert_case_trigger(
        WorkflowUUID.new(workflow.id), config, create_missing_tags=True
    )

    tags_service = CaseTagsService(session, role=svc_role)
    created = await tags_service.get_tag_by_ref("phishing")
    assert created.ref == "phishing"


@pytest.mark.anyio
async def test_case_trigger_rejects_unknown_tags(session: AsyncSession, svc_role):
    workflow = Workflow(
        title="Case Trigger Tags",
        description="Test workflow",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.flush()

    case_trigger = CaseTrigger(
        workspace_id=svc_role.workspace_id,
        workflow_id=workflow.id,
        status="offline",
        event_types=[],
        tag_filters=[],
    )
    session.add(case_trigger)
    await session.commit()

    service = CaseTriggersService(session, role=svc_role)
    with pytest.raises(TracecatNotFoundError):
        await service.update_case_trigger(
            WorkflowUUID.new(workflow.id),
            CaseTriggerUpdate(tag_filters=["missing-tag"]),
        )


@pytest.mark.anyio
async def test_case_trigger_update_clears_tag_filters_on_null(
    session: AsyncSession, svc_role
):
    workflow = Workflow(
        title="Case Trigger Tags",
        description="Test workflow",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.flush()

    case_trigger = CaseTrigger(
        workspace_id=svc_role.workspace_id,
        workflow_id=workflow.id,
        status="offline",
        event_types=[],
        tag_filters=["phishing"],
    )
    session.add(case_trigger)
    await session.commit()

    service = CaseTriggersService(session, role=svc_role)
    updated = await service.update_case_trigger(
        WorkflowUUID.new(workflow.id),
        CaseTriggerUpdate(tag_filters=None),
    )

    assert updated.tag_filters == []


@pytest.mark.anyio
async def test_get_case_trigger_backfills_missing_default(
    session: AsyncSession, svc_role
):
    workflow = Workflow(
        title="Case Trigger Backfill",
        description="Test workflow",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.commit()

    service = CaseTriggersService(session, role=svc_role)
    case_trigger = await service.get_case_trigger(WorkflowUUID.new(workflow.id))

    assert case_trigger.workflow_id == workflow.id
    assert case_trigger.status == "offline"
    assert case_trigger.event_types == []
    assert case_trigger.tag_filters == []


@pytest.mark.anyio
async def test_get_case_trigger_commit_false_does_not_persist_on_rollback(
    session: AsyncSession, svc_role
):
    workflow = Workflow(
        title="Case Trigger Rollback",
        description="Test workflow",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.commit()
    workflow_id = workflow.id

    service = CaseTriggersService(session, role=svc_role)
    case_trigger = await service._ensure_case_trigger_exists(
        WorkflowUUID.new(workflow_id), commit=False
    )

    assert case_trigger.workflow_id == workflow_id
    first_trigger_id = case_trigger.id

    await session.rollback()

    reloaded_service = CaseTriggersService(session, role=svc_role)
    refreshed = await reloaded_service._ensure_case_trigger_exists(
        WorkflowUUID.new(workflow_id), commit=False
    )

    assert refreshed.id != first_trigger_id


@pytest.mark.anyio
async def test_update_case_trigger_acquires_workflow_lock(
    session: AsyncSession, svc_role, monkeypatch
):
    workflow = Workflow(
        title="Case Trigger Lock",
        description="Test workflow",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.flush()

    case_trigger = CaseTrigger(
        workspace_id=svc_role.workspace_id,
        workflow_id=workflow.id,
        status="offline",
        event_types=[],
        tag_filters=[],
    )
    session.add(case_trigger)
    await session.commit()

    service = CaseTriggersService(session, role=svc_role)
    locked_workflow_ids: list[WorkflowUUID] = []

    async def _lock_workflow(workflow_id: WorkflowUUID) -> None:
        locked_workflow_ids.append(WorkflowUUID.new(workflow_id))

    monkeypatch.setattr(service, "_lock_workflow", _lock_workflow)
    await service.update_case_trigger(
        WorkflowUUID.new(workflow.id),
        CaseTriggerUpdate(status="offline"),
        commit=False,
    )

    assert locked_workflow_ids == [WorkflowUUID.new(workflow.id)]
