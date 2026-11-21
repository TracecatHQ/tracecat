"""Temporal e2e tests for per-organization agent tool-call and turn limits.

The real executor activity and the real ``run_agent_activity`` take leases
against real Redis and the tier tables. Only the bodies are stubs: the action
(``dispatch_action``) and the turn (``_run_agent_turn``), so tests can hold work
open and see what ran.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects.postgresql import insert as pg_insert
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowHandle, WorkflowHistory
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker
from tracecat_ee.agent.activities import (
    ApplyApprovalResultsActivityInputs,
    EmitSessionCancelledInputs,
    PersistApprovalsActivityInputs,
)
from tracecat_ee.agent.types import AgentWorkflowID
from tracecat_ee.agent.workflows.durable import (
    AgentWorkflowArgs,
    DurableAgentWorkflow,
    WorkflowApprovalSubmission,
    WorkflowCancelRequest,
)
from tracecat_ee.agent.workflows.registry_tool import ExecuteRegistryToolWorkflow

from tests.temporal.durable_agent_failure_harness import (
    mock_resolve_agent_config_activity,
)
from tests.temporal.test_durable_agent_workflow import (
    create_mock_build_tool_definitions_activity,
    create_mock_create_session_activity,
    create_mock_emit_session_done_activity,
    create_mock_finalize_turn_activity,
    create_mock_load_session_activity,
    create_mock_load_session_messages_activity,
    create_mock_reconcile_tool_results_activity,
)
from tracecat import config
from tracecat.admission import gate
from tracecat.admission.leases import LeaseStore
from tracecat.admission.limits import clear_limits_cache
from tracecat.admission.types import ConcurrencyResource, parse_not_admitted
from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.agent.executor import activity as agent_activity_module
from tracecat.agent.executor.activity import (
    AgentExecutorInput,
    AgentExecutorResult,
    run_agent_activity,
)
from tracecat.agent.mcp.executor import ActionExecutionError, execute_action
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.tokens import MCPTokenClaims
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Organization, OrganizationTier, Tier
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.dsl.schemas import RunActionInput
from tracecat.executor import activities as executor_activities
from tracecat.executor.activities import ExecutorActivities
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.redis import client as redis_client_module
from tracecat.redis.client import get_redis_client
from tracecat.registry.lock.types import RegistryLock
from tracecat.tiers.limits_cache import invalidate_effective_limits_cache

pytestmark = [pytest.mark.temporal, pytest.mark.usefixtures("db")]

TOOL_ACTION = "core.http_request"
TOOL_CALL_ID = "call-1"


# =============================================================================
# Fixtures and helpers
# =============================================================================


@pytest.fixture
async def limits_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[None, None]:
    """Turn the limits on and give the gate a fresh Redis client and caches."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(
        config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.WORKFLOW_CONCURRENCY_LIMITS}
    )
    monkeypatch.setattr(redis_client_module, "REDIS_URL", os.environ["REDIS_URL"])
    monkeypatch.setattr(config, "TRACECAT__AGENT_TOOL_CALL_CAPACITY_WAIT_SECONDS", 1.0)
    clear_limits_cache()
    gate.reset_breaker()
    redis_wrapper = await get_redis_client()
    await redis_wrapper.close()
    yield
    await redis_wrapper.close()
    clear_limits_cache()
    gate.reset_breaker()


async def _set_limits(
    org_id: uuid.UUID,
    *,
    tool_calls: int | None = None,
    agent_turns: int | None = None,
) -> None:
    """Upsert an organization tier with the given agent limits."""
    tier_id = uuid.uuid5(org_id, "temporal-agent-tier")
    tier_values = {
        "display_name": f"Agent Tier {org_id.hex[:8]}",
        "max_concurrent_workflows": None,
        "max_action_executions_per_workflow": None,
        "max_concurrent_actions": None,
        "max_concurrent_tool_calls": tool_calls,
        "max_concurrent_agent_turns": agent_turns,
        "api_rate_limit": None,
        "api_burst_capacity": None,
        "entitlements": {},
        "is_default": False,
        "sort_order": 0,
        "is_active": True,
    }
    async with get_async_session_context_manager() as session:
        await session.execute(
            pg_insert(Organization)
            .values(
                id=org_id,
                name=f"Agent Org {org_id.hex[:8]}",
                slug=f"agent-org-{org_id.hex}",
                is_active=True,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        await session.execute(
            pg_insert(Tier)
            .values(id=tier_id, **tier_values)
            .on_conflict_do_update(index_elements=["id"], set_=tier_values)
        )
        await session.execute(
            pg_insert(OrganizationTier)
            .values(organization_id=org_id, tier_id=tier_id)
            .on_conflict_do_update(
                index_elements=["organization_id"],
                set_={"tier_id": tier_id},
            )
        )
        await session.commit()
    await invalidate_effective_limits_cache(org_id)
    clear_limits_cache()


async def _in_use(org_id: uuid.UUID, resource: ConcurrencyResource) -> int:
    store = await LeaseStore.create()
    return await store.in_use(org_id, resource)


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 20) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.05)


async def _not_admitted_count(handle: WorkflowHandle[Any, Any], client: Client) -> int:
    count = 0
    async for event in handle.fetch_history_events():
        if event.event_type is not EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            continue
        payloads = event.activity_task_completed_event_attributes.result.payloads
        if not payloads:
            continue
        (result,) = await client.data_converter.decode(payloads)
        if parse_not_admitted(result) is not None:
            count += 1
    return count


async def _replay(client: Client, history: WorkflowHistory) -> None:
    async def histories() -> AsyncIterator[WorkflowHistory]:
        yield history

    with ThreadPoolExecutor() as workflow_task_executor:
        results = await Replayer(
            workflows=[DurableAgentWorkflow],
            workflow_task_executor=workflow_task_executor,
            workflow_runner=UnsandboxedWorkflowRunner(),
            data_converter=client.data_converter,
        ).replay_workflows(histories(), raise_on_replay_failure=False)
    assert not results.replay_failures


@dataclass
class FakeActions:
    """Stands in for ``dispatch_action``: holds an action open by its ``key`` arg."""

    holds: dict[str, asyncio.Event] = field(default_factory=dict)
    started: list[str] = field(default_factory=list)

    async def dispatch(
        self,
        *,
        backend: Any,
        input: RunActionInput,
        max_concurrency: int | None = None,
    ) -> dict[str, str]:
        del backend, max_concurrency
        key = str(input.task.args.get("key", input.task.ref))
        self.started.append(key)
        if (hold := self.holds.get(key)) is not None:
            await hold.wait()
        return {"key": key}


@pytest.fixture
def fake_actions(monkeypatch: pytest.MonkeyPatch) -> FakeActions:
    fake = FakeActions()
    monkeypatch.setattr(executor_activities, "dispatch_action", fake.dispatch)
    monkeypatch.setattr(
        executor_activities, "get_executor_backend", MagicMock(return_value=None)
    )
    return fake


@dataclass
class FakeTurns:
    """Stands in for ``_run_agent_turn``; the real activity still takes the lease."""

    holds: dict[uuid.UUID, asyncio.Event] = field(default_factory=dict)
    started: list[uuid.UUID] = field(default_factory=list)
    calls: Counter[uuid.UUID] = field(default_factory=Counter)
    approval_first: set[uuid.UUID] = field(default_factory=set)
    """Sessions whose first turn asks for approval of one tool call."""

    async def run(self, input: AgentExecutorInput) -> AgentExecutorResult:
        session_id = input.session_id
        self.started.append(session_id)
        self.calls[session_id] += 1
        if (hold := self.holds.get(session_id)) is not None:
            await hold.wait()
        if session_id in self.approval_first and self.calls[session_id] == 1:
            return AgentExecutorResult(
                success=True,
                approval_requested=True,
                approval_items=[
                    ToolCallContent(
                        id=TOOL_CALL_ID,
                        name="core__http_request",
                        input={"key": f"tool-{session_id}"},
                    )
                ],
            )
        return AgentExecutorResult(success=True, output={"done": str(session_id)})


@pytest.fixture
def fake_turns(monkeypatch: pytest.MonkeyPatch) -> FakeTurns:
    fake = FakeTurns()
    monkeypatch.setattr(agent_activity_module, "_run_agent_turn", fake.run)
    return fake


def _agent_activities(
    *, paused: asyncio.Event | None = None
) -> Sequence[Callable[..., Any]]:
    @activity.defn(name="record_approval_requests")
    async def record_approval_requests(input: PersistApprovalsActivityInputs) -> None:
        del input

    @activity.defn(name="apply_approval_decisions")
    async def apply_approval_decisions(
        input: ApplyApprovalResultsActivityInputs,
    ) -> None:
        del input

    @activity.defn(name="emit_session_cancelled")
    async def emit_session_cancelled(input: EmitSessionCancelledInputs) -> None:
        del input

    return [
        create_mock_create_session_activity(),
        create_mock_load_session_activity(),
        create_mock_load_session_messages_activity(),
        create_mock_build_tool_definitions_activity(),
        create_mock_reconcile_tool_results_activity(),
        create_mock_finalize_turn_activity(),
        create_mock_emit_session_done_activity(done_event=paused),
        record_approval_requests,
        apply_approval_decisions,
        emit_session_cancelled,
        run_agent_activity,
        ExecutorActivities.execute_action_activity,
        mock_resolve_agent_config_activity(),
    ]


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> str:
    name = f"test-agent-limits-{uuid.uuid4()}"
    monkeypatch.setattr(config, "TRACECAT__AGENT_QUEUE", name)
    monkeypatch.setattr(config, "TRACECAT__AGENT_EXECUTOR_QUEUE", name)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_QUEUE", name)
    return name


def _agent_worker(
    client: Client,
    queue: str,
    threadpool: ThreadPoolExecutor,
    *,
    paused: asyncio.Event | None = None,
) -> Worker:
    return Worker(
        client=client,
        task_queue=queue,
        activities=list(_agent_activities(paused=paused)),
        workflows=[DurableAgentWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
        activity_executor=threadpool,
        max_cached_workflows=0,
    )


async def _start_session(
    client: Client,
    queue: str,
    role: Role,
    session_id: uuid.UUID,
    *,
    approvals: bool = False,
) -> WorkflowHandle[Any, Any]:
    args = AgentWorkflowArgs(
        role=role,
        agent_args=RunAgentArgs(
            session_id=session_id,
            user_prompt="Test prompt",
            config=AgentConfig(
                model_name="claude-3-5-sonnet-20241022",
                model_provider="anthropic",
                actions=[TOOL_ACTION],
                tool_approvals={TOOL_ACTION: True} if approvals else None,
            ),
        ),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )
    return await client.start_workflow(
        DurableAgentWorkflow.run,
        args,
        id=AgentWorkflowID(session_id),
        task_queue=queue,
        retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        execution_timeout=timedelta(seconds=90),
    )


def _claims(role: Role, org_id: uuid.UUID) -> MCPTokenClaims:
    session_id = uuid.uuid4()
    assert role.workspace_id is not None
    return MCPTokenClaims(
        workspace_id=role.workspace_id,
        organization_id=org_id,
        session_id=session_id,
        parent_agent_workflow_id=f"agent/{session_id}",
        parent_agent_run_id="run-1",
        allowed_actions=[TOOL_ACTION],
    )


_LOCK = RegistryLock(
    origins={"tracecat_registry": "test-version"},
    actions={TOOL_ACTION: "tracecat_registry"},
)


# =============================================================================
# Tool calls
# =============================================================================


@pytest.mark.anyio
@pytest.mark.integration
async def test_live_tool_call_at_limit_gets_capacity_busy_other_org_unaffected(
    svc_role: Role,
    temporal_client: Client,
    threadpool: ThreadPoolExecutor,
    queue: str,
    limits_enabled: None,
    fake_actions: FakeActions,
) -> None:
    del limits_enabled
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    await _set_limits(org_a, tool_calls=1)
    await _set_limits(org_b, tool_calls=1)
    fake_actions.holds["a1"] = asyncio.Event()
    claims_a, claims_b = _claims(svc_role, org_a), _claims(svc_role, org_b)

    async with Worker(
        temporal_client,
        task_queue=queue,
        workflows=[ExecuteRegistryToolWorkflow],
        activities=[ExecutorActivities.execute_action_activity],
        workflow_runner=UnsandboxedWorkflowRunner(),
        activity_executor=threadpool,
    ):
        first = asyncio.create_task(
            execute_action(TOOL_ACTION, {"key": "a1"}, claims_a, _LOCK)
        )
        await _wait_until(lambda: "a1" in fake_actions.started)
        assert await _in_use(org_a, ConcurrencyResource.TOOL_CALLS) == 1

        # Org A is full: the call waits about a second, then the model gets a
        # retryable capacity error and nothing ran.
        started_at = time.monotonic()
        with pytest.raises(ActionExecutionError, match="Tool call not started"):
            await execute_action(TOOL_ACTION, {"key": "a2"}, claims_a, _LOCK)
        waited = time.monotonic() - started_at
        assert 0.9 <= waited < 10
        assert "a2" not in fake_actions.started

        # Org B has its own counter.
        started_at = time.monotonic()
        assert await execute_action(TOOL_ACTION, {"key": "b1"}, claims_b, _LOCK) == {
            "key": "b1"
        }
        assert time.monotonic() - started_at < 10

        fake_actions.holds["a1"].set()
        assert await first == {"key": "a1"}
        # Capacity is back once the first call released its lease.
        assert await execute_action(TOOL_ACTION, {"key": "a3"}, claims_a, _LOCK) == {
            "key": "a3"
        }

    assert await _in_use(org_a, ConcurrencyResource.TOOL_CALLS) == 0
    assert await _in_use(org_b, ConcurrencyResource.TOOL_CALLS) == 0


@pytest.mark.anyio
@pytest.mark.integration
async def test_approved_tool_call_waits_durably_for_tool_capacity(
    svc_role: Role,
    temporal_client: Client,
    threadpool: ThreadPoolExecutor,
    queue: str,
    limits_enabled: None,
    fake_actions: FakeActions,
    fake_turns: FakeTurns,
) -> None:
    del limits_enabled
    org_id = svc_role.organization_id
    assert org_id is not None
    await _set_limits(org_id, tool_calls=1)
    session_id = uuid.uuid4()
    fake_turns.approval_first.add(session_id)
    tool_key = f"tool-{session_id}"
    paused = asyncio.Event()

    # Another tool call holds the organization's only unit.
    store = await LeaseStore.create()
    await store.try_acquire(
        org_id,
        ConcurrencyResource.TOOL_CALLS,
        "other-tool-call",
        units=1,
        limit=1,
        ttl_seconds=90,
    )
    try:
        async with _agent_worker(temporal_client, queue, threadpool, paused=paused):
            handle = await _start_session(
                temporal_client, queue, svc_role, session_id, approvals=True
            )
            await asyncio.wait_for(paused.wait(), timeout=20)
            await handle.execute_update(
                DurableAgentWorkflow.set_approvals,
                WorkflowApprovalSubmission(
                    approvals={TOOL_CALL_ID: True},
                    approved_by=svc_role.user_id,
                ),
            )
            # The approved call is not admitted and waits on a durable timer.
            async with asyncio.timeout(20):
                while await _not_admitted_count(handle, temporal_client) == 0:
                    await asyncio.sleep(0.1)
            assert tool_key not in fake_actions.started

            await store.release(
                org_id, ConcurrencyResource.TOOL_CALLS, "other-tool-call", units=1
            )
            await asyncio.wait_for(handle.result(), timeout=60)

        assert fake_actions.started.count(tool_key) == 1
        assert fake_turns.calls[session_id] == 2  # approval turn + continuation
        await _replay(temporal_client, await handle.fetch_history())
    finally:
        await store.release(
            org_id, ConcurrencyResource.TOOL_CALLS, "other-tool-call", units=1
        )
    assert await _in_use(org_id, ConcurrencyResource.TOOL_CALLS) == 0


# =============================================================================
# Turns
# =============================================================================


@pytest.mark.anyio
@pytest.mark.integration
async def test_turn_waits_for_turn_capacity_until_another_turn_stops(
    svc_role: Role,
    temporal_client: Client,
    threadpool: ThreadPoolExecutor,
    queue: str,
    limits_enabled: None,
    fake_turns: FakeTurns,
) -> None:
    del limits_enabled
    org_id = svc_role.organization_id
    assert org_id is not None
    await _set_limits(org_id, agent_turns=1)
    first_session, second_session = uuid.uuid4(), uuid.uuid4()
    fake_turns.holds[first_session] = asyncio.Event()

    async with _agent_worker(temporal_client, queue, threadpool):
        first = await _start_session(temporal_client, queue, svc_role, first_session)
        await _wait_until(lambda: first_session in fake_turns.started)
        assert await _in_use(org_id, ConcurrencyResource.AGENT_TURNS) == 1

        second = await _start_session(temporal_client, queue, svc_role, second_session)
        async with asyncio.timeout(20):
            while await _not_admitted_count(second, temporal_client) == 0:
                await asyncio.sleep(0.1)
        await asyncio.sleep(1)
        assert second_session not in fake_turns.started

        fake_turns.holds[first_session].set()
        await asyncio.wait_for(first.result(), timeout=20)
        await asyncio.wait_for(second.result(), timeout=60)

    assert fake_turns.calls[second_session] == 1
    assert await _not_admitted_count(first, temporal_client) == 0
    assert await _in_use(org_id, ConcurrencyResource.AGENT_TURNS) == 0
    await _replay(temporal_client, await first.fetch_history())
    await _replay(temporal_client, await second.fetch_history())


@pytest.mark.anyio
@pytest.mark.integration
async def test_turn_waiting_on_approval_holds_no_turn(
    svc_role: Role,
    temporal_client: Client,
    threadpool: ThreadPoolExecutor,
    queue: str,
    limits_enabled: None,
    fake_turns: FakeTurns,
) -> None:
    del limits_enabled
    org_id = svc_role.organization_id
    assert org_id is not None
    await _set_limits(org_id, agent_turns=1)
    paused_session, other_session = uuid.uuid4(), uuid.uuid4()
    fake_turns.approval_first.add(paused_session)
    paused = asyncio.Event()

    async with _agent_worker(temporal_client, queue, threadpool, paused=paused):
        paused_handle = await _start_session(
            temporal_client, queue, svc_role, paused_session, approvals=True
        )
        await asyncio.wait_for(paused.wait(), timeout=20)
        assert await _in_use(org_id, ConcurrencyResource.AGENT_TURNS) == 0

        # With the limit at 1, another turn runs while the first waits on approval.
        other = await _start_session(temporal_client, queue, svc_role, other_session)
        await asyncio.wait_for(other.result(), timeout=20)
        assert await _not_admitted_count(other, temporal_client) == 0

        await _replay(temporal_client, await paused_handle.fetch_history())
        await paused_handle.terminate(reason="test done")


@pytest.mark.anyio
@pytest.mark.integration
async def test_cancel_ends_a_turn_capacity_wait(
    svc_role: Role,
    temporal_client: Client,
    threadpool: ThreadPoolExecutor,
    queue: str,
    limits_enabled: None,
    fake_turns: FakeTurns,
) -> None:
    del limits_enabled
    org_id = svc_role.organization_id
    assert org_id is not None
    await _set_limits(org_id, agent_turns=1)
    holder, waiter = uuid.uuid4(), uuid.uuid4()
    fake_turns.holds[holder] = asyncio.Event()

    async with _agent_worker(temporal_client, queue, threadpool):
        holder_handle = await _start_session(temporal_client, queue, svc_role, holder)
        await _wait_until(lambda: holder in fake_turns.started)
        waiter_handle = await _start_session(temporal_client, queue, svc_role, waiter)
        async with asyncio.timeout(20):
            while await _not_admitted_count(waiter_handle, temporal_client) == 0:
                await asyncio.sleep(0.1)

        cancelled_at = time.monotonic()
        await waiter_handle.execute_update(
            DurableAgentWorkflow.request_cancel,
            WorkflowCancelRequest(reason="user_cancel"),
        )
        await asyncio.wait_for(waiter_handle.result(), timeout=20)
        # Ends without waiting out the backoff; the turn never ran.
        assert time.monotonic() - cancelled_at < 5
        assert waiter not in fake_turns.started

        fake_turns.holds[holder].set()
        await asyncio.wait_for(holder_handle.result(), timeout=20)

    await _replay(temporal_client, await waiter_handle.fetch_history())
    assert await _in_use(org_id, ConcurrencyResource.AGENT_TURNS) == 0
