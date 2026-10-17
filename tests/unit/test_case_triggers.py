from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.enums import CaseEventType
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.models import CaseTrigger, Workflow, WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.case_triggers.schemas import CaseTriggerConfig, CaseTriggerUpdate
from tracecat.workflow.case_triggers.service import CaseTriggersService

pytestmark = pytest.mark.usefixtures("db")


def _runnable_definition_content() -> dict[str, Any]:
    dsl = DSLInput.model_validate(
        {
            "title": "Runnable workflow",
            "description": "Test workflow",
            "entrypoint": {"ref": "start"},
            "actions": [
                {
                    "ref": "start",
                    "action": "core.transform.reshape",
                    "args": {"value": "ok"},
                }
            ],
        }
    )
    return dsl.model_dump(exclude_unset=True)


def _case_closed_event_types() -> list[CaseEventType]:
    return [CaseEventType.CASE_CLOSED]


@pytest.mark.anyio
async def test_case_trigger_config_requires_event_types_when_online():
    with pytest.raises(ValidationError):
        CaseTriggerConfig(status="online", event_types=[], tag_filters=[])


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("workflow_version", "definition_version", "event_types", "error"),
    [
        pytest.param(1, 1, None, "", id="requires-events"),
        pytest.param(
            1,
            None,
            [CaseEventType.CASE_CLOSED],
            "Publish the workflow",
            id="requires-published-definition",
        ),
        pytest.param(
            2,
            1,
            [CaseEventType.CASE_CLOSED],
            "Publish the workflow",
            id="requires-current-definition",
        ),
        pytest.param(
            1, 1, [CaseEventType.CASE_CLOSED], None, id="allows-current-definition"
        ),
    ],
)
async def test_case_trigger_update_online_requirements(
    session: AsyncSession,
    svc_role,
    workflow_version: int,
    definition_version: int | None,
    event_types: list[CaseEventType] | None,
    error: str | None,
):
    workflow = Workflow(
        title="Case Trigger Test",
        description="Test workflow",
        status="offline",
        workspace_id=svc_role.workspace_id,
        version=workflow_version,
    )
    session.add(workflow)
    await session.flush()

    if definition_version is not None:
        session.add(
            WorkflowDefinition(
                workspace_id=svc_role.workspace_id,
                workflow_id=workflow.id,
                version=definition_version,
                content=_runnable_definition_content(),
            )
        )
    session.add(
        CaseTrigger(
            workspace_id=svc_role.workspace_id,
            workflow_id=workflow.id,
            status="offline",
            event_types=[],
            tag_filters=[],
        )
    )
    await session.commit()

    service = CaseTriggersService(session, role=svc_role)
    params = (
        CaseTriggerUpdate(status="online")
        if event_types is None
        else CaseTriggerUpdate(status="online", event_types=event_types)
    )
    if error is not None:
        with pytest.raises(TracecatValidationError, match=error):
            await service.update_case_trigger(WorkflowUUID.new(workflow.id), params)
        return

    updated = await service.update_case_trigger(WorkflowUUID.new(workflow.id), params)

    assert updated.status == "online"
    assert updated.event_types == ["case_closed"]


@pytest.mark.anyio
async def test_sync_case_trigger_allows_online_without_entitlement_check(
    session: AsyncSession, svc_role, monkeypatch: pytest.MonkeyPatch
):
    workflow = Workflow(
        title="Synced Case Trigger",
        description="Test workflow",
        status="offline",
        workspace_id=svc_role.workspace_id,
        version=1,
    )
    session.add(workflow)
    await session.flush()

    session.add(
        WorkflowDefinition(
            workspace_id=svc_role.workspace_id,
            workflow_id=workflow.id,
            version=1,
            content=_runnable_definition_content(),
        )
    )
    await session.commit()

    service = CaseTriggersService(session, role=svc_role)
    mock_require_entitlement = AsyncMock(side_effect=AssertionError)
    monkeypatch.setattr(service, "require_entitlement", mock_require_entitlement)
    updated = await service.sync_case_trigger(
        WorkflowUUID.new(workflow.id),
        CaseTriggerConfig(status="online", event_types=_case_closed_event_types()),
    )

    assert updated.status == "online"
    assert updated.event_types == ["case_closed"]
    mock_require_entitlement.assert_not_awaited()


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
