"""Runs linked to their trigger by ``TracecatTriggerId``.

The setter's registration gate, the start paths that set it, the query
filters, and the read side: a trigger's recent runs, its stopped events and
the trigger behind a run. Temporal is mocked; PostgreSQL is real.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import TypedSearchAttributes
from temporalio.service import RPCError, RPCStatusCode

from tracecat.agent.backends.base import AgentBackend
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.db.models import (
    AgentPreset,
    AgentSession,
    EventOutbox,
    Workflow,
    Workspace,
)
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.exceptions import ScopeDeniedError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import CursorPaginationParams, PaginationError
from tracecat.triggers.enums import (
    EventSourceKind,
    TriggerErrorCode,
    TriggerKind,
    TriggerTargetType,
)
from tracecat.triggers.exceptions import TriggerError
from tracecat.triggers.runs import service as runs_service
from tracecat.triggers.runs.service import DSL_WORKFLOW_TYPE, TriggerRunsService
from tracecat.triggers.schemas import (
    AwsSnsSourceConfig,
    EventSourceCreate,
    ObjectStorageTriggerConfig,
    TriggerCreate,
)
from tracecat.triggers.service import TriggersService
from tracecat.triggers.types import build_occurrence_key
from tracecat.workflow.executions import trigger_link
from tracecat.workflow.executions.common import build_query
from tracecat.workflow.executions.correlation import (
    build_agent_session_correlation_id,
)
from tracecat.workflow.executions.enums import TemporalSearchAttr
from tracecat.workflow.executions.schemas import WorkflowExecutionRelationFilter
from tracecat.workflow.executions.service import (
    WorkflowExecutionsPage,
    WorkflowExecutionsService,
)

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]


# ----- Registration gate -----


@pytest.fixture
def fresh_probe() -> Iterator[None]:
    with patch.object(trigger_link, "_probe", trigger_link._ProbeState()):
        yield


@pytest.fixture
def registered() -> Iterator[None]:
    """The namespace has the attribute (the probe already passed)."""
    with patch.object(trigger_link, "_probe", trigger_link._ProbeState(ready=True)):
        yield


@pytest.mark.usefixtures("fresh_probe")
async def test_pairs_are_set_once_the_probe_passes_and_cached() -> None:
    client = Mock(spec=Client)
    client.count_workflows = AsyncMock()
    trigger_id = uuid.uuid4()

    first = await trigger_link.trigger_id_pairs(client, trigger_id)
    second = await trigger_link.trigger_id_pairs(client, trigger_id)

    assert [(p.key, p.value) for p in first] == [
        (TemporalSearchAttr.TRIGGER_ID.key, str(trigger_id))
    ]
    assert second == first
    (call,) = client.count_workflows.await_args_list
    assert call.args == ("TracecatTriggerId = '00000000-0000-0000-0000-000000000000'",)


@pytest.mark.usefixtures("fresh_probe")
async def test_an_unregistered_attribute_sets_nothing_and_reprobes_later() -> None:
    client = Mock(spec=Client)
    client.count_workflows = AsyncMock(
        side_effect=RPCError("unknown", RPCStatusCode.INVALID_ARGUMENT, b"")
    )
    trigger_id = uuid.uuid4()
    clock = 1_000.0

    with patch.object(trigger_link.time, "monotonic", lambda: clock):
        assert await trigger_link.trigger_id_pairs(client, trigger_id) == []
        # Within the retry window: no second probe.
        assert await trigger_link.trigger_id_pairs(client, trigger_id) == []
        assert client.count_workflows.await_count == 1
        clock += trigger_link.PROBE_RETRY_SECONDS
        client.count_workflows.side_effect = None
        assert len(await trigger_link.trigger_id_pairs(client, trigger_id)) == 1
    assert client.count_workflows.await_count == 2


@pytest.mark.usefixtures("fresh_probe")
async def test_concurrent_first_starts_share_one_probe() -> None:
    client = Mock(spec=Client)
    release = asyncio.Event()

    async def _slow_count(*_: Any, **__: Any) -> None:
        await release.wait()

    client.count_workflows = AsyncMock(side_effect=_slow_count)
    trigger_ids = [uuid.uuid4() for _ in range(3)]
    calls = [
        asyncio.create_task(trigger_link.trigger_id_pairs(client, t))
        for t in trigger_ids
    ]
    await asyncio.sleep(0)
    release.set()

    results = await asyncio.gather(*calls)

    # Every start waited for the one probe and carries its trigger ID.
    assert [[p.value for p in r] for r in results] == [[str(t)] for t in trigger_ids]
    assert client.count_workflows.await_count == 1


@pytest.mark.usefixtures("fresh_probe")
async def test_no_trigger_means_no_probe() -> None:
    client = Mock(spec=Client)
    client.count_workflows = AsyncMock()

    assert await trigger_link.trigger_id_pairs(client, None) == []
    client.count_workflows.assert_not_awaited()


# ----- Start paths -----


def _dsl() -> DSLInput:
    return DSLInput(
        title="T",
        description="",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[ActionStatement(ref="a", action="core.transform.reshape", args={})],
    )


def _trigger_ids(search_attributes: TypedSearchAttributes) -> list[str]:
    return [
        pair.value
        for pair in search_attributes.search_attributes
        if pair.key == TemporalSearchAttr.TRIGGER_ID.key
    ]


@pytest.mark.usefixtures("registered")
@pytest.mark.parametrize(
    ("method", "temporal_method"),
    [
        ("create_workflow_execution_wait_for_start", "start_workflow"),
        ("create_workflow_execution", "execute_workflow"),
    ],
)
@pytest.mark.parametrize("with_trigger", [True, False])
async def test_workflow_starts_carry_the_trigger_id_only_when_given(
    svc_role: Role, method: str, temporal_method: str, with_trigger: bool
) -> None:
    client = Mock(spec=Client)
    setattr(client, temporal_method, AsyncMock(return_value=Mock()))
    service = WorkflowExecutionsService(client=client, role=svc_role)
    trigger_id = uuid.uuid4() if with_trigger else None

    with patch.object(service, "_resolve_execution_timeout", return_value=None):
        await getattr(service, method)(
            dsl=_dsl(), wf_id=WorkflowUUID.new_uuid4(), trigger_id=trigger_id
        )

    call = getattr(client, temporal_method).await_args
    expected = [str(trigger_id)] if trigger_id else []
    assert _trigger_ids(call.kwargs["search_attributes"]) == expected


@pytest.mark.usefixtures("fresh_probe")
async def test_a_start_before_registration_carries_no_trigger_id(
    svc_role: Role,
) -> None:
    client = Mock(spec=Client)
    client.count_workflows = AsyncMock(
        side_effect=RPCError("unknown", RPCStatusCode.INVALID_ARGUMENT, b"")
    )
    client.start_workflow = AsyncMock(return_value=Mock())
    service = WorkflowExecutionsService(client=client, role=svc_role)

    with patch.object(service, "_resolve_execution_timeout", return_value=None):
        await service.create_workflow_execution_wait_for_start(
            dsl=_dsl(), wf_id=WorkflowUUID.new_uuid4(), trigger_id=uuid.uuid4()
        )

    call = client.start_workflow.await_args
    assert call is not None
    assert _trigger_ids(call.kwargs["search_attributes"]) == []


def test_agent_turn_attributes_append_the_trigger_link() -> None:
    trigger_id = uuid.uuid4()
    context = Mock()
    context.session.id = uuid.uuid4()
    context.role.user_id = None
    context.role.workspace_id = uuid.uuid4()
    link = [TemporalSearchAttr.TRIGGER_ID.create_pair(str(trigger_id))]

    linked = AgentBackend._search_attributes(context, link)
    plain = AgentBackend._search_attributes(context)

    assert _trigger_ids(linked) == [str(trigger_id)]
    assert _trigger_ids(plain) == []
    # Everything else is unchanged.
    assert [p for p in linked.search_attributes if p not in link] == list(
        plain.search_attributes
    )


def test_build_query_rejects_a_quoted_workflow_type() -> None:
    with pytest.raises(ValueError, match="Invalid workflow type"):
        build_query(workspace_id=uuid.uuid4(), workflow_types={"x' OR '1'='1"})


def test_build_query_filters_by_trigger_and_workflow_type() -> None:
    workspace_id = uuid.uuid4()
    trigger_id = uuid.uuid4()

    query = build_query(
        workspace_id=workspace_id,
        trigger_id=trigger_id,
        workflow_types={DSL_WORKFLOW_TYPE},
    )

    assert f"TracecatWorkspaceId = '{workspace_id}'" in query
    assert f"TracecatTriggerId = '{trigger_id}'" in query
    assert "(WorkflowType = 'DSLWorkflow')" in query


# ----- Read side -----


@pytest.fixture(autouse=True)
def entitled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _entitled(self: TriggersService, entitlement: object) -> bool:
        return True

    monkeypatch.setattr(TriggersService, "has_entitlement", _entitled)


@dataclass
class FakeExecutions:
    """Stands in for ``WorkflowExecutionsService.connect``; records list calls."""

    items: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    execution: Any = None
    error: Exception | None = None

    async def list_executions_paginated(self, **kwargs: Any) -> WorkflowExecutionsPage:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return WorkflowExecutionsPage(
            items=self.items,
            next_cursor="next",
            prev_cursor=None,
            has_more=True,
            has_previous=False,
        )

    async def get_execution(self, wf_exec_id: str) -> Any:
        return self.execution


@pytest.fixture
def executions(registered: None) -> Iterator[FakeExecutions]:
    fake = FakeExecutions()
    with (
        patch.object(
            runs_service.WorkflowExecutionsService,
            "connect",
            AsyncMock(return_value=fake),
        ),
        patch.object(runs_service, "get_temporal_client", AsyncMock()),
    ):
        yield fake


def _execution(
    exec_id: str,
    workflow_type: str = DSL_WORKFLOW_TYPE,
    *,
    attrs: list[Any] | None = None,
) -> Mock:
    execution = Mock()
    execution.id = exec_id
    execution.workflow_type = workflow_type
    execution.status = WorkflowExecutionStatus.COMPLETED
    execution.start_time = datetime(2026, 11, 27, tzinfo=UTC)
    execution.close_time = datetime(2026, 11, 27, 0, 1, tzinfo=UTC)
    execution.typed_search_attributes = TypedSearchAttributes(
        search_attributes=attrs or []
    )
    return execution


async def _object_trigger(service: TriggersService, prefix: str = "") -> uuid.UUID:
    source = await service.create_event_source(
        EventSourceCreate(
            name="bucket",
            config=AwsSnsSourceConfig(
                kind=EventSourceKind.AWS_SNS,
                topic_arn=f"arn:aws:sns:us-east-1:123456789012:{uuid.uuid4().hex}",
            ),
        )
    )
    trigger = await service.create_trigger(
        TriggerCreate(
            name="Uploads",
            config=ObjectStorageTriggerConfig(
                kind=TriggerKind.OBJECT_STORAGE, source_id=source.id, key_prefix=prefix
            ),
        )
    )
    return trigger.id


def _role(workspace: Workspace, scopes: frozenset[str]) -> Role:
    return Role(
        type="user",
        workspace_id=workspace.id,
        organization_id=workspace.organization_id,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=scopes,
    )


async def test_recent_runs_read_the_trigger_root_runs_with_names(
    session: AsyncSession,
    svc_role: Role,
    svc_workspace: Workspace,
    executions: FakeExecutions,
) -> None:
    trigger_id = await _object_trigger(TriggersService(session, svc_role))
    workflow = Workflow(
        workspace_id=svc_workspace.id, title="On upload", description=""
    )
    preset = AgentPreset(
        workspace_id=svc_workspace.id,
        name="Triage",
        slug=f"triage-{uuid.uuid4().hex[:8]}",
        model_name="m",
        model_provider="p",
    )
    session.add_all([workflow, preset])
    await session.flush()
    # A trigger's session is titled after the trigger; the run shows the preset.
    agent_session = AgentSession(
        workspace_id=svc_workspace.id,
        title="Uploads",
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=preset.id,
        agent_preset_id=preset.id,
    )
    session.add(agent_session)
    await session.commit()
    wf_id = WorkflowUUID.new(workflow.id)
    session_id = agent_session.id
    executions.items = [
        _execution(f"{wf_id.short()}/exec_{uuid.uuid4().hex[:22]}"),
        _execution(
            f"agent-turn/{uuid.uuid4()}",
            "DurableAgentWorkflow",
            attrs=[
                TemporalSearchAttr.CORRELATION_ID.create_pair(
                    build_agent_session_correlation_id(session_id)
                )
            ],
        ),
    ]

    page = await TriggerRunsService(session, svc_role).list_runs(
        trigger_id, CursorPaginationParams(limit=10, cursor="c")
    )

    (call,) = executions.calls
    assert call["trigger_id"] == trigger_id
    assert call["relation"] is WorkflowExecutionRelationFilter.ROOT
    assert call["pagination"].cursor == "c"
    # An allowlist: workflow runs and agent turns, nothing else.
    assert call["workflow_types"] == {DSL_WORKFLOW_TYPE, "DurableAgentWorkflow"}
    assert "exclude_workflow_types" not in call
    workflow_run, agent_run = page.items
    assert workflow_run.target_type is TriggerTargetType.WORKFLOW
    assert workflow_run.target_name == "On upload"
    assert workflow_run.status == "COMPLETED"
    assert agent_run.target_type is TriggerTargetType.AGENT
    assert agent_run.session_id == session_id
    assert (agent_run.target_name, agent_run.agent_preset_id) == ("Triage", preset.id)
    assert (page.next_cursor, page.has_more) == ("next", True)


@pytest.mark.parametrize(
    ("run_scope", "workflow_types"),
    [
        ("workflow:read", {DSL_WORKFLOW_TYPE}),
        ("agent:read", {"DurableAgentWorkflow"}),
    ],
)
async def test_recent_runs_list_only_the_kinds_the_caller_may_read(
    session: AsyncSession,
    svc_role: Role,
    svc_workspace: Workspace,
    executions: FakeExecutions,
    run_scope: str,
    workflow_types: set[str],
) -> None:
    trigger_id = await _object_trigger(TriggersService(session, svc_role))
    role = _role(svc_workspace, frozenset({"trigger:read", run_scope}))

    await TriggerRunsService(session, role).list_runs(
        trigger_id, CursorPaginationParams(limit=10)
    )

    (call,) = executions.calls
    assert call["workflow_types"] == workflow_types


async def test_recent_runs_need_a_run_read_scope(
    session: AsyncSession,
    svc_role: Role,
    svc_workspace: Workspace,
    executions: FakeExecutions,
) -> None:
    trigger_id = await _object_trigger(TriggersService(session, svc_role))
    role = _role(svc_workspace, frozenset({"trigger:read"}))

    with pytest.raises(ScopeDeniedError):
        await TriggerRunsService(session, role).list_runs(
            trigger_id, CursorPaginationParams(limit=10)
        )
    assert executions.calls == []


async def test_a_trigger_in_another_workspace_is_not_found(
    session: AsyncSession,
    svc_role: Role,
    svc_workspace: Workspace,
    executions: FakeExecutions,
) -> None:
    trigger_id = await _object_trigger(TriggersService(session, svc_role))
    other = Workspace(name="other", organization_id=svc_workspace.organization_id)
    session.add(other)
    await session.commit()
    service = TriggerRunsService(session, _role(other, ADMIN_SCOPES))

    for read in (service.list_runs, service.list_stopped_events):
        with pytest.raises(TriggerError) as e:
            await read(trigger_id, CursorPaginationParams(limit=10))
        assert e.value.code is TriggerErrorCode.NOT_FOUND
    assert executions.calls == []


async def test_recent_runs_are_empty_only_while_the_attribute_is_unknown(
    session: AsyncSession, svc_role: Role, executions: FakeExecutions
) -> None:
    """Temporal rejects a query on an unknown attribute: no run carries it yet.
    Any other failure is an error, never an empty history."""
    trigger_id = await _object_trigger(TriggersService(session, svc_role))
    service = TriggerRunsService(session, svc_role)
    executions.error = RPCError("unknown", RPCStatusCode.INVALID_ARGUMENT, b"")

    page = await service.list_runs(trigger_id, CursorPaginationParams(limit=10))
    assert page.items == []
    # With a cursor the same rejection is a bad cursor, not "no runs".
    with pytest.raises(ValueError, match="Invalid cursor"):
        await service.list_runs(
            trigger_id, CursorPaginationParams(limit=10, cursor="forged")
        )

    executions.error = RPCError("down", RPCStatusCode.UNAVAILABLE, b"")
    with pytest.raises(RPCError):
        await service.list_runs(trigger_id, CursorPaginationParams(limit=10))


# ----- Stopped events -----


def _outbox_row(
    workspace_id: uuid.UUID, source_id: uuid.UUID, key: str, **values: Any
) -> EventOutbox:
    return EventOutbox(
        workspace_id=workspace_id,
        kind="object_storage",
        source_id=source_id,
        occurrence_key=build_occurrence_key(
            bucket="uploads", key=key, version_id=None, sequencer=uuid.uuid4().hex
        ),
        payload={"bucket": "uploads", "key": key},
        **values,
    )


async def _stop(session: AsyncSession, *rows: EventOutbox) -> None:
    """Stop rows the way the dispatcher does: ``next_attempt_at = NULL``.

    Set after insert: the column's server default would replace a None.
    """
    await session.flush()
    await session.execute(
        update(EventOutbox)
        .where(EventOutbox.id.in_([row.id for row in rows]))
        .values(next_attempt_at=None)
    )
    await session.commit()


async def test_stopped_events_match_the_trigger_and_hide_secrets_and_the_event(
    session: AsyncSession, svc_role: Role, svc_workspace: Workspace
) -> None:
    triggers = TriggersService(session, svc_role)
    trigger_id = await _object_trigger(triggers, prefix="in/")
    trigger = await triggers.get_trigger(trigger_id)
    assert isinstance(trigger.config, ObjectStorageTriggerConfig)
    source_id = trigger.config.source_id
    secret = "sk-proj-" + "a" * 40
    key = "in/quarterly-report.csv"
    stopped = _outbox_row(
        svc_workspace.id,
        source_id,
        key,
        attempts=8,
        last_error=f"binding x: RuntimeError: Bearer {secret} while reading {key}",
    )
    # Still retrying, or outside the prefix: not listed.
    retrying = _outbox_row(svc_workspace.id, source_id, "in/b.csv", attempts=2)
    outside = _outbox_row(svc_workspace.id, source_id, "out/c.csv")
    session.add_all([stopped, retrying, outside])
    await _stop(session, stopped, outside)

    page = await TriggerRunsService(session, svc_role).list_stopped_events(
        trigger_id, CursorPaginationParams(limit=10)
    )

    (event,) = page.items
    assert event.attempts == 8
    # The global row ID never leaves the server.
    assert "id" not in event.model_dump()
    assert event.error is not None
    assert secret not in event.error
    assert key not in event.error
    assert "RuntimeError" in event.error
    assert page.has_more is False


async def test_stopped_events_page_newest_first(
    session: AsyncSession, svc_role: Role, svc_workspace: Workspace
) -> None:
    triggers = TriggersService(session, svc_role)
    trigger_id = await _object_trigger(triggers)
    trigger = await triggers.get_trigger(trigger_id)
    assert isinstance(trigger.config, ObjectStorageTriggerConfig)
    rows = [
        _outbox_row(svc_workspace.id, trigger.config.source_id, f"k{i}", attempts=i)
        for i in range(3)
    ]
    session.add_all(rows)
    await _stop(session, *rows)
    service = TriggerRunsService(session, svc_role)

    first = await service.list_stopped_events(
        trigger_id, CursorPaginationParams(limit=2)
    )
    second = await service.list_stopped_events(
        trigger_id, CursorPaginationParams(limit=2, cursor=first.next_cursor)
    )

    # Same created_at (one transaction), so the occurrence key breaks ties.
    newest_first = sorted((r.occurrence_key for r in rows), reverse=True)
    assert len(first.items) == 2 and first.has_more is True
    assert first.next_cursor is not None
    # The cursor names (received, occurrence key), never the global row ID.
    _, occurrence_key = runs_service._decode_stopped_cursor(first.next_cursor)
    assert occurrence_key in {r.occurrence_key for r in rows}
    assert len(second.items) == 1 and second.has_more is False
    attempts_by_key = {r.occurrence_key: r.attempts for r in rows}
    assert [e.attempts for e in [*first.items, *second.items]] == [
        attempts_by_key[k] for k in newest_first
    ]
    with pytest.raises(PaginationError):
        await service.list_stopped_events(
            trigger_id, CursorPaginationParams(limit=2, cursor="not-a-cursor")
        )


async def test_stopped_events_need_a_run_read_scope(
    session: AsyncSession, svc_role: Role, svc_workspace: Workspace
) -> None:
    trigger_id = await _object_trigger(TriggersService(session, svc_role))
    role = _role(svc_workspace, frozenset({"trigger:read"}))

    with pytest.raises(ScopeDeniedError):
        await TriggerRunsService(session, role).list_stopped_events(
            trigger_id, CursorPaginationParams(limit=10)
        )
    # Either run read scope is enough.
    page = await TriggerRunsService(
        session, _role(svc_workspace, frozenset({"trigger:read", "agent:read"}))
    ).list_stopped_events(trigger_id, CursorPaginationParams(limit=10))
    assert page.items == []


# ----- Origin -----


async def test_a_run_and_a_session_lead_back_to_their_trigger(
    session: AsyncSession, svc_role: Role, executions: FakeExecutions
) -> None:
    trigger_id = await _object_trigger(TriggersService(session, svc_role))
    executions.execution = _execution(
        "wf/exec",
        attrs=[TemporalSearchAttr.TRIGGER_ID.create_pair(str(trigger_id))],
    )
    service = TriggerRunsService(session, svc_role)

    origin = await service.run_origin(wf_exec_id="wf/exec")

    assert origin is not None
    assert (origin.trigger_id, origin.name, origin.kind) == (
        trigger_id,
        "Uploads",
        TriggerKind.OBJECT_STORAGE,
    )

    turn = _execution(
        "turn",
        "DurableAgentWorkflow",
        attrs=[TemporalSearchAttr.TRIGGER_ID.create_pair(str(trigger_id))],
    )
    client = Mock(spec=Client)
    queries: list[str] = []

    async def _list(query: str, **_: Any) -> Any:
        queries.append(query)
        yield turn

    client.list_workflows = _list
    session_id = uuid.uuid4()
    with patch.object(
        runs_service, "get_temporal_client", AsyncMock(return_value=client)
    ):
        by_session = await service.run_origin(session_id=session_id)

    assert by_session is not None and by_session.trigger_id == trigger_id
    (query,) = queries
    assert build_agent_session_correlation_id(session_id) in query
    assert "TracecatTriggerId IS NOT NULL" in query
    assert "WorkflowType = 'DurableAgentWorkflow'" in query
    assert "DSLWorkflow" not in query
    assert f"TracecatWorkspaceId = '{svc_role.workspace_id}'" in query


async def test_a_run_without_the_attribute_or_a_deleted_trigger(
    session: AsyncSession, svc_role: Role, executions: FakeExecutions
) -> None:
    service = TriggerRunsService(session, svc_role)
    executions.execution = _execution("wf/exec")
    assert await service.run_origin(wf_exec_id="wf/exec") is None

    gone = uuid.uuid4()
    executions.execution = _execution(
        "wf/exec", attrs=[TemporalSearchAttr.TRIGGER_ID.create_pair(str(gone))]
    )
    origin = await service.run_origin(wf_exec_id="wf/exec")
    assert origin is not None
    assert (origin.trigger_id, origin.name) == (gone, None)


async def test_origin_by_run_id_answers_only_for_workflow_runs(
    session: AsyncSession, svc_workspace: Workspace, executions: FakeExecutions
) -> None:
    """An agent turn's ID must not answer for a caller without agent:read."""
    role = _role(svc_workspace, frozenset({"trigger:read", "workflow:read"}))
    trigger_id = uuid.uuid4()
    executions.execution = _execution(
        f"agent/{uuid.uuid4()}",
        "DurableAgentWorkflow",
        attrs=[TemporalSearchAttr.TRIGGER_ID.create_pair(str(trigger_id))],
    )

    origin = await TriggerRunsService(session, role).run_origin(
        wf_exec_id=executions.execution.id
    )

    assert origin is None


async def test_origin_needs_the_run_kind_read_scope(
    session: AsyncSession, svc_workspace: Workspace, executions: FakeExecutions
) -> None:
    role = _role(svc_workspace, frozenset({"trigger:read", "agent:read"}))
    with pytest.raises(ScopeDeniedError):
        await TriggerRunsService(session, role).run_origin(wf_exec_id="wf/exec")
    with pytest.raises(ValueError):
        await TriggerRunsService(session, role).run_origin()
