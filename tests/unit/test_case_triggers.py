import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.models import CaseTrigger, Workflow
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
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
            workflow.id, CaseTriggerUpdate(status="online")
        )


@pytest.mark.anyio
async def test_case_trigger_create_missing_tags(
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
        workflow.id, config, create_missing_tags=True
    )

    tags_service = CaseTagsService(session, role=svc_role)
    created = await tags_service.get_tag_by_ref("phishing")
    assert created.ref == "phishing"


@pytest.mark.anyio
async def test_case_trigger_rejects_unknown_tags(
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
        tag_filters=[],
    )
    session.add(case_trigger)
    await session.commit()

    service = CaseTriggersService(session, role=svc_role)
    with pytest.raises(TracecatNotFoundError):
        await service.update_case_trigger(
            workflow.id,
            CaseTriggerUpdate(tag_filters=["missing-tag"]),
        )
