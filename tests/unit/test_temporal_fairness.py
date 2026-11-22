"""Organization fairness keys on Temporal workflow starts and schedules."""

from __future__ import annotations

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.common import Priority
from temporalio.service import RPCError, RPCStatusCode

from tracecat.agent.workflows.tool_execution import AGENT_TOOL_PRIORITY
from tracecat.auth.types import Role
from tracecat.cases.agent_invocations import queue
from tracecat.db.models import Schedule as ScheduleRow
from tracecat.db.models import Workflow, Workspace
from tracecat.db.session_events import AfterCommitQueue
from tracecat.identifiers import ScheduleUUID
from tracecat.identifiers.workflow import WorkflowUUID, WorkspaceUUID
from tracecat.temporal.fairness import org_priority
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.fairness_backfill import (
    ScheduleOwner,
    backfill_schedule_fairness_keys,
    list_schedule_owners,
)


def _role(organization_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-service",
        workspace_id=WorkspaceUUID.new_uuid4(),
        organization_id=organization_id,
    )


# ---------------------------------------------------------------------------
# org_priority
# ---------------------------------------------------------------------------


def test_org_priority_sets_org_fairness_key() -> None:
    org_id = uuid.uuid4()

    priority = org_priority(org_id)

    assert priority.fairness_key == str(org_id)
    assert priority.priority_key is None
    assert priority.fairness_weight is None


def test_org_priority_keeps_base_priority_key() -> None:
    org_id = uuid.uuid4()

    priority = org_priority(org_id, AGENT_TOOL_PRIORITY)

    assert priority == Priority(priority_key=1, fairness_key=str(org_id))
    assert AGENT_TOOL_PRIORITY.fairness_key is None


def test_org_priority_without_org_returns_base() -> None:
    assert org_priority(None, AGENT_TOOL_PRIORITY) is AGENT_TOOL_PRIORITY


# ---------------------------------------------------------------------------
# Start paths not covered by their own module's tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_new_schedule_action_carries_org_fairness_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    captured: dict[str, Schedule] = {}

    class _FakeTemporalClient:
        async def create_schedule(self, id: str, schedule: Schedule) -> Any:
            captured["schedule"] = schedule
            return SimpleNamespace(id=id)

    async def _get_temporal_client() -> _FakeTemporalClient:
        return _FakeTemporalClient()

    monkeypatch.setattr(bridge, "get_temporal_client", _get_temporal_client)

    await bridge.create_schedule(
        workflow_id=WorkflowUUID.new_uuid4(),
        schedule_id=ScheduleUUID.new_uuid4(),
        role=_role(org_id),
        every=timedelta(hours=1),
    )

    action = captured["schedule"].action
    assert isinstance(action, ScheduleActionStartWorkflow)
    assert action.priority == Priority(fairness_key=str(org_id))


@pytest.mark.anyio
async def test_case_comment_invocation_start_carries_org_fairness_key() -> None:
    org_id = uuid.uuid4()
    session = cast(Session, SimpleNamespace(info={}))
    client = SimpleNamespace(start_workflow=AsyncMock())

    queue.invoke_comment_agent_turns_after_commit(
        cast(Any, session), invocation_ids=[uuid.uuid4()], role=_role(org_id)
    )
    (dispatch,) = AfterCommitQueue.of(session).callbacks
    with patch.object(queue, "get_temporal_client", AsyncMock(return_value=client)):
        await dispatch()

    client.start_workflow.assert_awaited_once()
    priority = client.start_workflow.await_args.kwargs["priority"]
    assert priority == Priority(fairness_key=str(org_id))


# ---------------------------------------------------------------------------
# Schedule backfill
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_schedule_owners_maps_schedule_to_its_org(
    session: AsyncSession, svc_workspace: Workspace
) -> None:
    workflow = Workflow(
        title="Fairness backfill", description="", workspace_id=svc_workspace.id
    )
    session.add(workflow)
    await session.flush()
    schedule = ScheduleRow(
        workspace_id=svc_workspace.id,
        workflow_id=workflow.id,
        every=timedelta(hours=1),
        inputs={},
    )
    session.add(schedule)
    await session.flush()

    owners = await list_schedule_owners(session)

    assert (
        ScheduleOwner(
            schedule_id=schedule.id, organization_id=svc_workspace.organization_id
        )
        in owners
    )


def _unkeyed_action() -> ScheduleActionStartWorkflow:
    return ScheduleActionStartWorkflow(
        "DSLWorkflow",
        id="wf/sch",
        task_queue="tracecat-task-queue",
    )


class _FakeScheduleHandle:
    """Applies ``handle.update`` callbacks the way the Temporal SDK does."""

    def __init__(self, action: Any, *, missing: bool = False) -> None:
        self.action = action
        self.missing = missing
        self.writes = 0

    @property
    def priority(self) -> Priority:
        assert isinstance(self.action, ScheduleActionStartWorkflow)
        return self.action.priority

    async def update(self, updater: Any) -> None:
        if self.missing:
            raise RPCError("schedule not found", RPCStatusCode.NOT_FOUND, b"")
        schedule = Schedule(
            action=copy.deepcopy(self.action),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(1))]),
        )
        update_input = SimpleNamespace(description=SimpleNamespace(schedule=schedule))
        update = updater(cast(ScheduleUpdateInput, update_input))
        if update is None:
            return
        assert isinstance(update, ScheduleUpdate)
        self.action = update.schedule.action
        self.writes += 1


class _FailingScheduleHandle(_FakeScheduleHandle):
    async def update(self, updater: Any) -> None:
        raise RuntimeError("boom")


def _client(handles: dict[uuid.UUID, _FakeScheduleHandle]) -> Client:
    by_temporal_id = {
        ScheduleUUID.new(schedule_id).to_legacy(): handle
        for schedule_id, handle in handles.items()
    }
    return cast(Client, SimpleNamespace(get_schedule_handle=by_temporal_id.__getitem__))


@pytest.mark.anyio
async def test_backfill_dry_run_writes_nothing() -> None:
    owner = ScheduleOwner(schedule_id=uuid.uuid4(), organization_id=uuid.uuid4())
    handle = _FakeScheduleHandle(_unkeyed_action())

    counts = await backfill_schedule_fairness_keys(
        _client({owner.schedule_id: handle}), [owner], dry_run=True
    )

    assert counts["updated"] == 1
    assert handle.writes == 0
    assert handle.priority == Priority.default


@pytest.mark.anyio
async def test_backfill_sets_key_once_and_keeps_priority_key() -> None:
    owner = ScheduleOwner(schedule_id=uuid.uuid4(), organization_id=uuid.uuid4())
    action = _unkeyed_action()
    action.priority = Priority(priority_key=2)
    handle = _FakeScheduleHandle(action)
    client = _client({owner.schedule_id: handle})

    first = await backfill_schedule_fairness_keys(client, [owner], dry_run=False)
    second = await backfill_schedule_fairness_keys(client, [owner], dry_run=False)

    assert first["updated"] == 1
    assert second["already_keyed"] == 1
    assert second["updated"] == 0
    assert handle.writes == 1
    assert handle.priority == Priority(
        priority_key=2, fairness_key=str(owner.organization_id)
    )


@pytest.mark.anyio
async def test_backfill_replaces_a_wrong_key() -> None:
    owner = ScheduleOwner(schedule_id=uuid.uuid4(), organization_id=uuid.uuid4())
    action = _unkeyed_action()
    action.priority = Priority(fairness_key="other-org")
    handle = _FakeScheduleHandle(action)

    counts = await backfill_schedule_fairness_keys(
        _client({owner.schedule_id: handle}), [owner], dry_run=False
    )

    assert counts["updated"] == 1
    assert handle.priority.fairness_key == str(owner.organization_id)


@pytest.mark.anyio
async def test_backfill_counts_missing_unsupported_and_failed_and_continues() -> None:
    missing = ScheduleOwner(schedule_id=uuid.uuid4(), organization_id=uuid.uuid4())
    unsupported = ScheduleOwner(schedule_id=uuid.uuid4(), organization_id=uuid.uuid4())
    failing = ScheduleOwner(schedule_id=uuid.uuid4(), organization_id=uuid.uuid4())
    ok = ScheduleOwner(schedule_id=uuid.uuid4(), organization_id=uuid.uuid4())
    ok_handle = _FakeScheduleHandle(_unkeyed_action())
    client = _client(
        {
            missing.schedule_id: _FakeScheduleHandle(None, missing=True),
            unsupported.schedule_id: _FakeScheduleHandle(object()),
            failing.schedule_id: _FailingScheduleHandle(_unkeyed_action()),
            ok.schedule_id: ok_handle,
        }
    )

    counts = await backfill_schedule_fairness_keys(
        client, [missing, unsupported, failing, ok], dry_run=False
    )

    assert counts == {"missing": 1, "unsupported": 1, "failed": 1, "updated": 1}
    assert ok_handle.priority.fairness_key == str(ok.organization_id)
