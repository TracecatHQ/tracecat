"""Organization fairness keys on Temporal workflow starts and schedules."""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from temporalio.api.common.v1 import Priority as PriorityProto
from temporalio.api.schedule.v1 import Schedule as ScheduleProto
from temporalio.api.workflowservice.v1 import (
    DescribeScheduleRequest,
    DescribeScheduleResponse,
    UpdateScheduleRequest,
)
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleSpec,
)
from temporalio.common import Priority
from temporalio.service import RPCError, RPCStatusCode

from tracecat.agent.workflows.tool_execution import AGENT_TOOL_PRIORITY
from tracecat.auth.types import Role
from tracecat.cases.agent_invocations import queue
from tracecat.db.models import Schedule as ScheduleRow
from tracecat.db.models import Workflow, Workspace
from tracecat.db.session_events import AfterCommitQueue
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.common import DSLRunArgs
from tracecat.identifiers import ScheduleUUID
from tracecat.identifiers.workflow import WorkflowUUID, WorkspaceUUID
from tracecat.temporal.fairness import org_priority
from tracecat.workflow.schedules import bridge, fairness_backfill
from tracecat.workflow.schedules.fairness_backfill import (
    ScheduleOwner,
    backfill_schedule_fairness_keys,
    list_schedule_owners,
)
from tracecat.workflow.schedules.schemas import ScheduleUpdate as ScheduleUpdateParams


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
async def test_schedule_edit_keeps_org_fairness_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An edit rewrites the whole action, so it sets the key from the run's role."""
    org_id = uuid.uuid4()
    run_args = DSLRunArgs(role=_role(org_id), wf_id=WorkflowUUID.new_uuid4())
    action = ScheduleActionStartWorkflow(
        "DSLWorkflow", id="wf/sch", task_queue="tracecat-task-queue"
    )
    action.args = await get_data_converter().encode([run_args])
    action.priority = Priority(priority_key=2)
    schedule = Schedule(action=action, spec=ScheduleSpec())
    updates: list[Any] = []

    class _FakeHandle:
        async def update(self, updater: Any) -> None:
            update_input = SimpleNamespace(
                description=SimpleNamespace(schedule=schedule)
            )
            updates.append(await updater(update_input))

    async def _get_handle(_: Any) -> _FakeHandle:
        return _FakeHandle()

    monkeypatch.setattr(bridge, "_get_handle", _get_handle)

    await bridge.update_schedule(ScheduleUUID.new_uuid4(), ScheduleUpdateParams())

    (update,) = updates
    assert update.schedule.action.priority == Priority(
        priority_key=2, fairness_key=str(org_id)
    )


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


class _FakeScheduleService:
    """Describe and update schedules the way Temporal does, conflict tokens included.

    Temporal drops an update whose conflict token is stale. ``concurrent_edits``
    simulates a user pausing the schedule right after each of the next N reads.
    """

    def __init__(self) -> None:
        self.schedules: dict[str, ScheduleProto] = {}
        self.tokens: dict[str, int] = {}
        self.writes = 0
        self.concurrent_edits = 0
        self.reject_stale_writes = False

    def add(self, schedule_id: uuid.UUID, schedule: ScheduleProto) -> ScheduleProto:
        temporal_id = ScheduleUUID.new(schedule_id).to_legacy()
        self.schedules[temporal_id] = schedule
        self.tokens[temporal_id] = 1
        return schedule

    async def describe_schedule(
        self, req: DescribeScheduleRequest, *, retry: bool = False
    ) -> DescribeScheduleResponse:
        if req.schedule_id not in self.schedules:
            raise RPCError("schedule not found", RPCStatusCode.NOT_FOUND, b"")
        response = DescribeScheduleResponse(
            conflict_token=self.tokens[req.schedule_id].to_bytes(8, "big")
        )
        response.schedule.CopyFrom(self.schedules[req.schedule_id])
        if self.concurrent_edits > 0:
            self.concurrent_edits -= 1
            self.schedules[req.schedule_id].state.paused = True
            self.tokens[req.schedule_id] += 1
        return response

    async def update_schedule(
        self, req: UpdateScheduleRequest, *, retry: bool = False
    ) -> None:
        token = int.from_bytes(req.conflict_token, "big")
        if token != self.tokens[req.schedule_id]:
            if self.reject_stale_writes:
                raise RPCError(
                    "conflict token mismatch", RPCStatusCode.FAILED_PRECONDITION, b""
                )
            return
        self.schedules[req.schedule_id].CopyFrom(req.schedule)
        self.tokens[req.schedule_id] += 1
        self.writes += 1


async def _schedule(
    organization_id: uuid.UUID, priority: PriorityProto | None = None
) -> ScheduleProto:
    run_args = DSLRunArgs(role=_role(organization_id), wf_id=WorkflowUUID.new_uuid4())
    schedule = ScheduleProto()
    start = schedule.action.start_workflow
    start.workflow_id = "wf/sch"
    start.workflow_type.name = "DSLWorkflow"
    start.task_queue.name = "tracecat-task-queue"
    start.input.payloads.extend(await get_data_converter().encode([run_args]))
    if priority is not None:
        start.priority.CopyFrom(priority)
    return schedule


def _owner() -> ScheduleOwner:
    return ScheduleOwner(schedule_id=uuid.uuid4(), organization_id=uuid.uuid4())


def _client(service: _FakeScheduleService) -> Client:
    return cast(
        Client,
        SimpleNamespace(namespace="default", identity="test", workflow_service=service),
    )


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fairness_backfill, "_RETRY_DELAY_SECONDS", 0)


@pytest.mark.anyio
async def test_backfill_dry_run_writes_nothing() -> None:
    owner = _owner()
    service = _FakeScheduleService()
    schedule = service.add(owner.schedule_id, await _schedule(owner.organization_id))

    counts = await backfill_schedule_fairness_keys(
        _client(service), [owner], dry_run=True
    )

    assert counts == {"updated": 1}
    assert service.writes == 0
    assert schedule.action.start_workflow.priority.fairness_key == ""


@pytest.mark.anyio
async def test_backfill_sets_only_the_key_once() -> None:
    owner = _owner()
    service = _FakeScheduleService()
    schedule = service.add(
        owner.schedule_id,
        await _schedule(
            owner.organization_id,
            PriorityProto(priority_key=2, fairness_weight=2.0),
        ),
    )
    client = _client(service)

    first = await backfill_schedule_fairness_keys(client, [owner], dry_run=False)
    second = await backfill_schedule_fairness_keys(client, [owner], dry_run=False)

    assert first == {"updated": 1}
    assert second == {"already_keyed": 1}
    assert service.writes == 1
    assert schedule.action.start_workflow.priority == PriorityProto(
        priority_key=2, fairness_key=str(owner.organization_id), fairness_weight=2.0
    )


@pytest.mark.anyio
async def test_backfill_replaces_a_wrong_key() -> None:
    owner = _owner()
    service = _FakeScheduleService()
    schedule = service.add(
        owner.schedule_id,
        await _schedule(owner.organization_id, PriorityProto(fairness_key="other")),
    )

    counts = await backfill_schedule_fairness_keys(
        _client(service), [owner], dry_run=False
    )

    assert counts == {"updated": 1}
    key = schedule.action.start_workflow.priority.fairness_key
    assert key == str(owner.organization_id)


@pytest.mark.anyio
@pytest.mark.parametrize("reject_stale_writes", [False, True])
async def test_backfill_keeps_a_pause_made_after_its_read(
    reject_stale_writes: bool,
) -> None:
    """The stale write is dropped or rejected; the backfill reads again and keys the paused schedule."""
    owner = _owner()
    service = _FakeScheduleService()
    schedule = service.add(owner.schedule_id, await _schedule(owner.organization_id))
    service.concurrent_edits = 1
    service.reject_stale_writes = reject_stale_writes

    counts = await backfill_schedule_fairness_keys(
        _client(service), [owner], dry_run=False
    )

    assert counts == {"updated": 1}
    assert service.writes == 1
    assert schedule.state.paused is True
    key = schedule.action.start_workflow.priority.fairness_key
    assert key == str(owner.organization_id)


@pytest.mark.anyio
async def test_backfill_fails_a_schedule_that_keeps_changing() -> None:
    owner = _owner()
    service = _FakeScheduleService()
    schedule = service.add(owner.schedule_id, await _schedule(owner.organization_id))
    service.concurrent_edits = 100

    counts = await backfill_schedule_fairness_keys(
        _client(service), [owner], dry_run=False
    )

    assert counts == {"failed": 1}
    assert service.writes == 0
    assert schedule.action.start_workflow.priority.fairness_key == ""


@pytest.mark.anyio
async def test_backfill_skips_a_schedule_owned_by_another_org() -> None:
    owner = _owner()
    service = _FakeScheduleService()
    service.add(owner.schedule_id, await _schedule(uuid.uuid4()))

    for dry_run in (True, False):
        counts = await backfill_schedule_fairness_keys(
            _client(service), [owner], dry_run=dry_run
        )
        assert counts == {"owner_mismatch": 1}
    assert service.writes == 0


@pytest.mark.anyio
async def test_backfill_reports_a_keyed_schedule_owned_by_another_org() -> None:
    owner = _owner()
    service = _FakeScheduleService()
    service.add(
        owner.schedule_id,
        await _schedule(
            uuid.uuid4(), PriorityProto(fairness_key=str(owner.organization_id))
        ),
    )

    counts = await backfill_schedule_fairness_keys(
        _client(service), [owner], dry_run=False
    )

    assert counts == {"owner_mismatch": 1}


@pytest.mark.anyio
async def test_backfill_counts_missing_unsupported_and_failed_and_continues() -> None:
    missing, unsupported, undecodable, ok = _owner(), _owner(), _owner(), _owner()
    service = _FakeScheduleService()
    service.add(unsupported.schedule_id, ScheduleProto())
    broken = await _schedule(undecodable.organization_id)
    del broken.action.start_workflow.input.payloads[:]
    service.add(undecodable.schedule_id, broken)
    ok_schedule = service.add(ok.schedule_id, await _schedule(ok.organization_id))

    counts = await backfill_schedule_fairness_keys(
        _client(service), [missing, unsupported, undecodable, ok], dry_run=False
    )

    assert counts == {"missing": 1, "unsupported": 1, "failed": 1, "updated": 1}
    key = ok_schedule.action.start_workflow.priority.fairness_key
    assert key == str(ok.organization_id)
