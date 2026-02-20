import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ResponseError
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import Future, RetryError

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.triggers.consumer import CaseTriggerConsumer
from tracecat.db.models import CaseTrigger, Workflow

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_case_trigger_consumer_matches_event_type(
    session: AsyncSession, svc_role
):
    workflow = Workflow(
        title="Case Trigger Consumer",
        description="Test workflow",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.flush()

    case_trigger = CaseTrigger(
        workspace_id=svc_role.workspace_id,
        workflow_id=workflow.id,
        status="online",
        event_types=["case_created"],
        tag_filters=[],
    )
    session.add(case_trigger)
    await session.commit()

    consumer = CaseTriggerConsumer(client=AsyncMock())
    matched = await consumer._load_triggers(
        session, svc_role.workspace_id, "case_created"
    )
    assert len(matched) == 1

    unmatched = await consumer._load_triggers(
        session, svc_role.workspace_id, "case_closed"
    )
    assert unmatched == []


def _build_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        user_id=None,
        service_id="tracecat-case-triggers",
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-case-triggers"],
    )


def _build_consumer_with_mocks(
    client: AsyncMock,
    *,
    event,
    case,
    triggers,
    role: Role,
    dispatch: AsyncMock | None = None,
) -> CaseTriggerConsumer:
    consumer = CaseTriggerConsumer(client=client)
    consumer._load_event = AsyncMock(return_value=event)
    consumer._load_case = AsyncMock(return_value=case)
    consumer._load_triggers = AsyncMock(return_value=triggers)
    consumer._get_service_role = AsyncMock(return_value=role)
    if dispatch is not None:
        consumer._dispatch_workflow = dispatch
    return consumer


@pytest.mark.anyio
async def test_case_trigger_consumer_lock_prevents_ack():
    event_id = uuid.uuid4()
    case_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()

    event = SimpleNamespace(
        id=event_id,
        data={},
        created_at=None,
        type="case_created",
        user_id=None,
    )
    case = SimpleNamespace(
        id=case_id,
        workspace_id=workspace_id,
        tags=[],
    )
    trigger = SimpleNamespace(
        workflow_id=workflow_id,
        tag_filters=[],
    )

    client = AsyncMock()
    client.exists = AsyncMock(return_value=False)
    client.set_if_not_exists = AsyncMock(return_value=False)

    dispatch = AsyncMock(return_value=True)
    consumer = _build_consumer_with_mocks(
        client,
        event=event,
        case=case,
        triggers=[trigger],
        role=_build_role(workspace_id),
        dispatch=dispatch,
    )

    fields = {
        "event_id": str(event_id),
        "case_id": str(case_id),
        "workspace_id": str(workspace_id),
        "event_type": "case_created",
    }
    should_ack = await consumer._process_message(fields)
    assert should_ack is False
    dispatch.assert_not_called()


@pytest.mark.anyio
async def test_case_trigger_consumer_done_allows_ack():
    event_id = uuid.uuid4()
    case_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()

    event = SimpleNamespace(
        id=event_id,
        data={},
        created_at=None,
        type="case_created",
        user_id=None,
    )
    case = SimpleNamespace(
        id=case_id,
        workspace_id=workspace_id,
        tags=[],
    )
    trigger = SimpleNamespace(
        workflow_id=workflow_id,
        tag_filters=[],
    )

    client = AsyncMock()
    client.exists = AsyncMock(return_value=True)
    client.set_if_not_exists = AsyncMock(return_value=True)

    dispatch = AsyncMock(return_value=True)
    consumer = _build_consumer_with_mocks(
        client,
        event=event,
        case=case,
        triggers=[trigger],
        role=_build_role(workspace_id),
        dispatch=dispatch,
    )

    fields = {
        "event_id": str(event_id),
        "case_id": str(case_id),
        "workspace_id": str(workspace_id),
        "event_type": "case_created",
    }
    should_ack = await consumer._process_message(fields)
    assert should_ack is True
    dispatch.assert_not_called()


@pytest.mark.anyio
async def test_case_trigger_consumer_missing_definition_no_ack():
    event_id = uuid.uuid4()
    case_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()

    event = SimpleNamespace(
        id=event_id,
        data={},
        created_at=None,
        type="case_created",
        user_id=None,
    )
    case = SimpleNamespace(
        id=case_id,
        workspace_id=workspace_id,
        tags=[],
    )
    trigger = SimpleNamespace(
        workflow_id=workflow_id,
        tag_filters=[],
    )

    client = AsyncMock()
    client.exists = AsyncMock(return_value=False)
    client.set_if_not_exists = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)

    consumer = _build_consumer_with_mocks(
        client,
        event=event,
        case=case,
        triggers=[trigger],
        role=_build_role(workspace_id),
    )

    fields = {
        "event_id": str(event_id),
        "case_id": str(case_id),
        "workspace_id": str(workspace_id),
        "event_type": "case_created",
    }
    should_ack = await consumer._process_message(fields)
    assert should_ack is False


def _nogroup_retry_error() -> RetryError:
    future = Future(1)
    future.set_exception(
        ResponseError(
            "NOGROUP No such key 'case-events' or consumer group 'case-triggers' "
            "in XREADGROUP with GROUP option"
        )
    )
    return RetryError(future)


@pytest.mark.anyio
async def test_case_trigger_consumer_recovers_from_nogroup():
    client = AsyncMock()
    client.xreadgroup = AsyncMock(
        side_effect=[_nogroup_retry_error(), asyncio.CancelledError()]
    )
    consumer = CaseTriggerConsumer(client=client)
    consumer._ensure_group = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await consumer.run()

    assert consumer._ensure_group.await_count == 2
