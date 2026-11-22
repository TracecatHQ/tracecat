"""Temporal e2e tests for per-organization action concurrency limits.

The real executor activity runs the admission gate against real Redis and the
tier tables; only the action body (``dispatch_action``) is a stub, so tests can
hold an action open and count what ran.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects.postgresql import insert as pg_insert
from temporalio.api.enums.v1 import EventType, IndexedValueType
from temporalio.api.history.v1 import HistoryEvent
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.client import WorkflowFailureError, WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat import config
from tracecat.admission import gate
from tracecat.admission.leases import LeaseStore
from tracecat.admission.limits import clear_limits_cache
from tracecat.admission.types import (
    ADMISSION_ACK_SIGNAL,
    ConcurrencyResource,
    NotAdmittedReason,
    parse_not_admitted,
)
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Organization, OrganizationTier, Tier
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.schemas import ActionRetryPolicy, ActionStatement, RunActionInput
from tracecat.executor import activities as executor_activities
from tracecat.executor.activities import ExecutorActivities
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.redis import client as redis_client_module
from tracecat.redis.client import get_redis_client
from tracecat.runtime.errors import RuntimeErrorKind, RuntimeErrorOwner
from tracecat.temporal.errors import extract_error_classification
from tracecat.tiers.limits_cache import invalidate_effective_limits_cache
from tracecat.workflow.executions.enums import TemporalSearchAttr
from tracecat.workflow.executions.service import WorkflowExecutionsService

pytestmark = [
    pytest.mark.temporal,
    pytest.mark.usefixtures("registry_version_with_manifest"),
]

EXECUTE_ACTION = ExecutorActivities.execute_action_activity.__name__


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter(compression_enabled=False)
    ) as workflow_env:
        await workflow_env.client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                search_attributes={
                    TemporalSearchAttr.ERROR_OWNER.value: (
                        IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
                    )
                }
            )
        )
        yield workflow_env


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
    # The time-skipping server advances its clock past real-time heartbeats.
    monkeypatch.setattr(config, "TRACECAT__ACTIVITY_HEARTBEAT_TIMEOUT", 0)
    clear_limits_cache()
    gate.reset_breaker()
    redis_wrapper = await get_redis_client()
    await redis_wrapper.close()
    yield
    await redis_wrapper.close()
    clear_limits_cache()
    gate.reset_breaker()


@dataclass
class FakeActions:
    """Stands in for ``dispatch_action``: optionally holds an action open."""

    holds: dict[str, asyncio.Event] = field(default_factory=dict)
    started: list[str] = field(default_factory=list)
    running: Counter[str] = field(default_factory=Counter)
    max_running: Counter[str] = field(default_factory=Counter)
    widths: dict[str, int | None] = field(default_factory=dict)
    on_start: Callable[[str], Awaitable[None]] | None = None
    """Runs when an action starts, before its result is returned."""
    results: dict[str, dict[str, str]] = field(default_factory=dict)

    async def dispatch(
        self,
        *,
        backend: Any,
        input: RunActionInput,
        max_concurrency: int | None = None,
    ) -> dict[str, str]:
        del backend
        role = ctx_role.get()
        org = str(role.organization_id if role else None)
        ref = input.task.ref
        self.started.append(ref)
        self.widths[ref] = max_concurrency
        self.running[org] += 1
        self.max_running[org] = max(self.max_running[org], self.running[org])
        try:
            if self.on_start is not None:
                await self.on_start(ref)
            if (hold := self.holds.get(ref)) is not None:
                await hold.wait()
            return self.results.get(ref, {"ref": ref})
        finally:
            self.running[org] -= 1


@pytest.fixture
def fake_actions(monkeypatch: pytest.MonkeyPatch) -> FakeActions:
    fake = FakeActions()
    monkeypatch.setattr(executor_activities, "dispatch_action", fake.dispatch)
    monkeypatch.setattr(
        executor_activities, "get_executor_backend", MagicMock(return_value=None)
    )
    return fake


def _dsl(title: str, refs: list[str], *, chain: bool = False) -> DSLInput:
    """Actions run in parallel, or one after another with ``chain``."""
    actions = [
        ActionStatement(
            ref=ref,
            action="core.transform.reshape",
            args={"value": ref},
            depends_on=[refs[i - 1]] if chain and i > 0 else [],
        )
        for i, ref in enumerate(refs)
    ]
    return DSLInput(
        title=title,
        description=title,
        entrypoint=DSLEntrypoint(ref=refs[0]),
        actions=actions,
    )


async def _configure_org_limits(
    org_id: uuid.UUID,
    *,
    max_concurrent_actions: int | None = None,
    max_action_executions_per_workflow: int | None = None,
) -> None:
    """Upsert an organization tier with the given limits."""
    tier_id = uuid.uuid5(org_id, "temporal-tier")
    tier_values = {
        "display_name": f"Temporal Tier {org_id.hex[:8]}",
        "max_concurrent_workflows": None,
        "max_action_executions_per_workflow": max_action_executions_per_workflow,
        "max_concurrent_actions": max_concurrent_actions,
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
                name=f"Temporal Org {org_id.hex[:8]}",
                slug=f"temporal-org-{org_id.hex}",
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
            .values(
                organization_id=org_id,
                tier_id=tier_id,
                max_concurrent_workflows=None,
                max_action_executions_per_workflow=None,
                max_concurrent_actions=None,
                api_rate_limit=None,
                api_burst_capacity=None,
                entitlement_overrides=None,
            )
            .on_conflict_do_update(
                index_elements=["organization_id"],
                set_={"tier_id": tier_id},
            )
        )
        await session.commit()
    await invalidate_effective_limits_cache(org_id)


def _role_for_org(base_role: Role, org_id: uuid.UUID) -> Role:
    return base_role.model_copy(update={"organization_id": org_id})


async def _start(
    env: WorkflowEnvironment, role: Role, dsl: DSLInput, name: str
) -> WorkflowHandle[Any, Any]:
    return await env.client.start_workflow(
        "DSLWorkflow",
        DSLRunArgs(dsl=dsl, role=role, wf_id=TEST_WF_ID),
        id=generate_test_exec_id(f"{name}_{uuid.uuid4().hex[:6]}"),
        task_queue=config.TEMPORAL__CLUSTER_QUEUE,
    )


def _workers(
    env: WorkflowEnvironment, test_worker_factory: Callable[..., Worker]
) -> tuple[Worker, Worker]:
    return (
        test_worker_factory(env.client),
        test_worker_factory(
            env.client,
            activities=[ExecutorActivities.execute_action_activity],
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
        ),
    )


async def _history(handle: WorkflowHandle[Any, Any]) -> list[HistoryEvent]:
    return [event async for event in handle.fetch_history_events()]


async def _busy_count(handle: WorkflowHandle[Any, Any]) -> int:
    converter = get_data_converter(compression_enabled=False)
    count = 0
    for event in await _history(handle):
        if event.event_type is not EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            continue
        payloads = event.activity_task_completed_event_attributes.result.payloads
        if not payloads:
            continue
        (result,) = await converter.decode(payloads)
        if parse_not_admitted(result) is not None:
            count += 1
    return count


async def _wait_until(predicate: Callable[[], Any], *, timeout: float = 20) -> None:
    async with asyncio.timeout(timeout):
        while not await predicate():
            await asyncio.sleep(0.05)


def _scheduled_activity_types(history: list[HistoryEvent]) -> list[str]:
    return [
        e.activity_task_scheduled_event_attributes.activity_type.name
        for e in history
        if e.event_type is EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]


def _admission_signals(history: list[HistoryEvent]) -> int:
    return sum(
        1
        for e in history
        if e.event_type is EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED
        and e.workflow_execution_signaled_event_attributes.signal_name
        == ADMISSION_ACK_SIGNAL
    )


def _fired_timer_count(history: list[HistoryEvent]) -> int:
    return sum(1 for e in history if e.event_type is EventType.EVENT_TYPE_TIMER_FIRED)


def _timers(history: list[HistoryEvent]) -> list[timedelta]:
    return [
        e.timer_started_event_attributes.start_to_fire_timeout.ToTimedelta()
        for e in history
        if e.event_type is EventType.EVENT_TYPE_TIMER_STARTED
    ]


@pytest.mark.anyio
async def test_cap_one_second_action_waits_then_runs_and_bounces_are_hidden(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    limits_enabled: None,
    fake_actions: FakeActions,
) -> None:
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_actions=1)
    fake_actions.holds["first"] = asyncio.Event()

    dsl_worker, executor_worker = _workers(env, test_worker_factory)
    # Real time: an activity is held open, so the server must not skip ahead.
    with env.auto_time_skipping_disabled():
        async with dsl_worker, executor_worker:
            holder = await _start(env, test_role, _dsl("holder", ["first"]), "holder")

            async def first_started() -> bool:
                return "first" in fake_actions.started

            await _wait_until(first_started)
            waiter = await _start(env, test_role, _dsl("waiter", ["second"]), "waiter")

            async def waiter_bounced() -> bool:
                return await _busy_count(waiter) >= 1

            await _wait_until(waiter_bounced)
            assert "second" not in fake_actions.started

            fake_actions.holds["first"].set()
            await holder.result()
            await waiter.result()

    assert fake_actions.started == ["first", "second"]
    assert fake_actions.max_running[str(org_id)] == 1

    # Both history views show one execution of "second", with no busy bounce.
    service = WorkflowExecutionsService(env.client, role=test_role)
    service.require_execution = AsyncMock()  # visibility isn't needed here
    compact = await service.list_workflow_execution_events_compact(waiter.id)
    second_compact = [e for e in compact if e.action_ref == "second"]
    assert len(second_compact) == 1
    assert second_compact[0].action_result is not None
    assert parse_not_admitted(second_compact[0].action_result) is None

    detailed = await service.list_workflow_execution_events(waiter.id)
    action_events = [
        e
        for e in detailed
        if e.event_group is not None and e.event_group.action_ref == "second"
    ]
    assert [e.event_type.value for e in action_events] == [
        "ACTIVITY_TASK_SCHEDULED",
        "ACTIVITY_TASK_STARTED",
        "ACTIVITY_TASK_COMPLETED",
    ]
    assert await _busy_count(waiter) >= 1  # raw history still has the bounce
    # The admitted probe told the run it got in.
    assert _admission_signals(await _history(waiter)) == 1


@pytest.mark.anyio
async def test_capped_org_waits_while_other_org_starts_at_once(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    limits_enabled: None,
    fake_actions: FakeActions,
) -> None:
    capped_org = uuid.uuid4()
    other_org = uuid.uuid4()
    await _configure_org_limits(capped_org, max_concurrent_actions=1)
    await _configure_org_limits(other_org, max_concurrent_actions=1)
    capped_role = _role_for_org(test_role, capped_org)
    other_role = _role_for_org(test_role, other_org)
    fake_actions.holds["capped_1"] = asyncio.Event()
    fake_actions.holds["other_1"] = asyncio.Event()

    dsl_worker, executor_worker = _workers(env, test_worker_factory)
    # Real time: an activity is held open, so the server must not skip ahead.
    with env.auto_time_skipping_disabled():
        async with dsl_worker, executor_worker:
            first = await _start(env, capped_role, _dsl("c1", ["capped_1"]), "c1")

            async def capped_started() -> bool:
                return "capped_1" in fake_actions.started

            await _wait_until(capped_started)
            second = await _start(env, capped_role, _dsl("c2", ["capped_2"]), "c2")
            other = await _start(env, other_role, _dsl("o1", ["other_1"]), "o1")

            async def other_started() -> bool:
                return "other_1" in fake_actions.started

            await _wait_until(other_started, timeout=10)
            assert "capped_2" not in fake_actions.started

            fake_actions.holds["capped_1"].set()
            fake_actions.holds["other_1"].set()
            await asyncio.gather(first.result(), second.result(), other.result())

    assert await _busy_count(other) == 0
    assert await _busy_count(second) >= 1
    assert fake_actions.max_running[str(capped_org)] == 1


@pytest.mark.anyio
async def test_long_wait_keeps_waiting_with_bounded_history(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    limits_enabled: None,
    fake_actions: FakeActions,
) -> None:
    """A 20-minute wait never fails, and costs one probe per backoff period."""
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_actions=1)
    refs = ["w1", "w2", "w3"]

    # Another holder keeps the org full for the whole wait.
    store = await LeaseStore.create()
    await store.try_acquire(
        org_id,
        ConcurrencyResource.ACTIONS,
        "external-holder",
        units=1,
        limit=1,
        ttl_seconds=90,
    )
    stop_renewing = asyncio.Event()

    async def keep_holding() -> None:
        while not stop_renewing.is_set():
            await store.renew(
                org_id,
                ConcurrencyResource.ACTIONS,
                "external-holder",
                units=1,
                ttl_seconds=90,
            )
            await asyncio.sleep(1)

    renewer = asyncio.create_task(keep_holding())
    dsl_worker, executor_worker = _workers(env, test_worker_factory)
    try:
        async with dsl_worker, executor_worker:
            handle = await _start(env, test_role, _dsl("long", refs), "long")

            async def all_bounced() -> bool:
                return await _busy_count(handle) >= len(refs)

            await _wait_until(all_bounced)
            await env.sleep(timedelta(minutes=20))
            assert fake_actions.started == []

            stop_renewing.set()
            await renewer
            await store.release(
                org_id, ConcurrencyResource.ACTIONS, "external-holder", units=1
            )
            await handle.result()
    finally:
        stop_renewing.set()
        await asyncio.gather(renewer, return_exceptions=True)

    assert sorted(fake_actions.started) == refs
    history = await _history(handle)
    bounces = await _busy_count(handle)
    fired = _fired_timer_count(history)
    waited = history[-1].event_time.ToDatetime() - history[0].event_time.ToDatetime()
    print(f"long wait: waited={waited} bounces={bounces} fired_timers={fired}")
    assert waited >= timedelta(minutes=15)
    assert max(_timers(history)) <= timedelta(seconds=60)
    # One shared timer: about 20 min / 60 s periods plus the ramp-up, not one
    # timer per waiting action.
    assert fired <= 45
    # Each fired timer allows one probe; on top, the first wave (one per action)
    # and at most one hand-off probe per action once capacity frees.
    assert bounces <= fired + 2 * len(refs)
    executed = [t for t in _scheduled_activity_types(history) if t == EXECUTE_ACTION]
    assert len(executed) == bounces + len(refs)


@pytest.mark.anyio
async def test_redis_down_runs_the_action(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    limits_enabled: None,
    fake_actions: FakeActions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_actions=1)
    redis_wrapper = await get_redis_client()
    await redis_wrapper.close()
    monkeypatch.setattr(redis_client_module, "REDIS_URL", "redis://localhost:1/0")

    dsl_worker, executor_worker = _workers(env, test_worker_factory)
    async with dsl_worker, executor_worker:
        handle = await _start(env, test_role, _dsl("no-redis", ["solo"]), "noredis")
        await handle.result()

    assert fake_actions.started == ["solo"]
    assert await _busy_count(handle) == 0
    await redis_wrapper.close()


@pytest.mark.anyio
async def test_uncontended_run_records_no_extra_activities(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    limits_enabled: None,
    fake_actions: FakeActions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_actions=5)
    dsl = _dsl("uncontended", ["a", "b"], chain=True)

    dsl_worker, executor_worker = _workers(env, test_worker_factory)
    async with dsl_worker, executor_worker:
        limited = await _start(env, test_role, dsl, "limited")
        await limited.result()
        monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", set())
        baseline = await _start(env, test_role, dsl, "baseline")
        await baseline.result()

    limited_history = await _history(limited)
    baseline_history = await _history(baseline)
    limited_types = _scheduled_activity_types(limited_history)
    assert limited_types == _scheduled_activity_types(baseline_history)
    assert limited_types.count(EXECUTE_ACTION) == 2
    assert "get_tier_limits_activity" not in limited_types
    assert _timers(limited_history) == []
    assert _admission_signals(limited_history) == 0
    assert len(limited_history) == len(baseline_history)


@pytest.mark.anyio
async def test_action_budget_still_enforced_from_the_start_of_run_snapshot(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    limits_enabled: None,
    fake_actions: FakeActions,
) -> None:
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_action_executions_per_workflow=1)

    dsl_worker, executor_worker = _workers(env, test_worker_factory)
    async with dsl_worker, executor_worker:
        handle = await _start(
            env, test_role, _dsl("budget", ["one", "two"], chain=True), "budget"
        )
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.TENANT_QUOTA_EXHAUSTED
    assert fake_actions.started == ["one"]
    assert "get_tier_limits_activity" not in _scheduled_activity_types(
        await _history(handle)
    )


@pytest.mark.anyio
async def test_rerun_never_reuses_a_busy_answer(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    limits_enabled: None,
    fake_actions: FakeActions,
) -> None:
    """A run cancelled while waiting offers no fixed result for the waiting action."""
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_actions=1)
    store = await LeaseStore.create()
    await store.try_acquire(
        org_id,
        ConcurrencyResource.ACTIONS,
        "external-holder",
        units=1,
        limit=1,
        ttl_seconds=90,
    )

    dsl_worker, executor_worker = _workers(env, test_worker_factory)
    try:
        async with dsl_worker, executor_worker:
            handle = await _start(env, test_role, _dsl("cancel", ["waits"]), "cancel")

            async def bounced() -> bool:
                return await _busy_count(handle) >= 1

            await _wait_until(bounced)
            await handle.cancel()
            with pytest.raises(WorkflowFailureError):
                await handle.result()
    finally:
        await store.release(
            org_id, ConcurrencyResource.ACTIONS, "external-holder", units=1
        )

    assert fake_actions.started == []
    service = WorkflowExecutionsService(env.client, role=test_role)
    service.require_execution = AsyncMock()
    source = await service.get_rerun_source(handle.id)
    assert source.actions["waits"].fixed is None


@pytest.mark.anyio
async def test_lost_admission_signal_still_completes(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    limits_enabled: None,
    fake_actions: FakeActions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no admission signal, the probe token passes on when the probe completes."""
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_actions=2)
    monkeypatch.setattr(executor_activities, "_ack_admission", AsyncMock())
    store = await LeaseStore.create()
    await store.try_acquire(
        org_id,
        ConcurrencyResource.ACTIONS,
        "external-holder",
        units=2,
        limit=2,
        ttl_seconds=90,
    )
    refs = ["p1", "p2"]

    dsl_worker, executor_worker = _workers(env, test_worker_factory)
    try:
        async with dsl_worker, executor_worker:
            handle = await _start(env, test_role, _dsl("lost", refs), "lost")

            async def both_bounced() -> bool:
                return await _busy_count(handle) >= len(refs)

            await _wait_until(both_bounced)
            await store.release(
                org_id, ConcurrencyResource.ACTIONS, "external-holder", units=2
            )
            await handle.result()
    finally:
        await store.release(
            org_id, ConcurrencyResource.ACTIONS, "external-holder", units=2
        )

    assert sorted(fake_actions.started) == refs
    assert _admission_signals(await _history(handle)) == 0


@pytest.mark.anyio
async def test_for_each_never_runs_wider_than_the_cap(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    limits_enabled: None,
    fake_actions: FakeActions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A for_each wider than the org's cap reserves, and runs, only the cap."""
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_actions=2)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_FOR_EACH_MAX_CONCURRENCY", 6)
    store = await LeaseStore.create()
    in_use_while_running: list[int] = []

    async def record_in_use(_ref: str) -> None:
        in_use_while_running.append(
            await store.in_use(org_id, ConcurrencyResource.ACTIONS)
        )

    fake_actions.on_start = record_in_use
    dsl = DSLInput(
        title="fanout",
        description="fanout",
        entrypoint=DSLEntrypoint(ref="fanout"),
        actions=[
            ActionStatement(
                ref="fanout",
                action="core.transform.reshape",
                args={"value": "${{ var.x }}"},
                for_each="${{ for var.x in [1, 2, 3, 4, 5, 6, 7, 8] }}",
            )
        ],
    )

    dsl_worker, executor_worker = _workers(env, test_worker_factory)
    async with dsl_worker, executor_worker:
        handle = await _start(env, test_role, dsl, "fanout")
        await handle.result()

    assert fake_actions.widths["fanout"] == 2
    assert in_use_while_running == [2]
    assert await store.in_use(org_id, ConcurrencyResource.ACTIONS) == 0


@pytest.mark.anyio
async def test_rerun_keeps_the_last_real_attempt_after_a_busy_bounce(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    limits_enabled: None,
    fake_actions: FakeActions,
) -> None:
    """retry_until ran once, then bounced: rerun reuses the attempt that ran."""
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_actions=2)
    store = await LeaseStore.create()

    async def fill_the_org(_ref: str) -> None:
        # While this attempt holds 1 unit, take the rest of the cap and more,
        # so the next retry_until iteration bounces.
        await store.try_acquire(
            org_id,
            ConcurrencyResource.ACTIONS,
            "external-holder",
            units=2,
            limit=3,
            ttl_seconds=90,
        )

    fake_actions.on_start = fill_the_org
    fake_actions.results["retry_action"] = {"status": "loading"}
    dsl = DSLInput(
        title="retry",
        description="retry",
        entrypoint=DSLEntrypoint(ref="retry_action"),
        actions=[
            ActionStatement(
                ref="retry_action",
                action="core.transform.reshape",
                args={"value": "x"},
                retry_policy=ActionRetryPolicy(
                    retry_until="${{ ACTIONS.retry_action.result.status == 'done' }}"
                ),
            )
        ],
    )

    dsl_worker, executor_worker = _workers(env, test_worker_factory)
    try:
        async with dsl_worker, executor_worker:
            handle = await _start(env, test_role, dsl, "retry")

            async def bounced() -> bool:
                return await _busy_count(handle) >= 1

            await _wait_until(bounced)
            await handle.cancel()
            with pytest.raises(WorkflowFailureError):
                await handle.result()
    finally:
        await store.release(
            org_id, ConcurrencyResource.ACTIONS, "external-holder", units=2
        )

    assert fake_actions.started == ["retry_action"]
    service = WorkflowExecutionsService(env.client, role=test_role)
    service.require_execution = AsyncMock()
    source = await service.get_rerun_source(handle.id)
    assert source.actions["retry_action"].fixed is not None


@pytest.mark.anyio
async def test_overloaded_executor_defers_without_using_a_retry(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    limits_enabled: None,
    fake_actions: FakeActions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Redis timeout on a lagging loop defers the action; Redis stays enforced."""
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_actions=1)

    lagging = MagicMock()
    lagging.max_lag_seconds = MagicMock(return_value=1.0)
    monkeypatch.setattr(gate, "loop_lag", lagging)
    real_try_acquire = LeaseStore.try_acquire
    timeouts_left = 2

    async def slow_then_real(self: LeaseStore, *args: Any, **kwargs: Any) -> Any:
        nonlocal timeouts_left
        if timeouts_left > 0:
            timeouts_left -= 1
            raise TimeoutError
        return await real_try_acquire(self, *args, **kwargs)

    monkeypatch.setattr(LeaseStore, "try_acquire", slow_then_real)
    dsl = DSLInput(
        title="overloaded",
        description="overloaded",
        entrypoint=DSLEntrypoint(ref="solo"),
        actions=[
            ActionStatement(
                ref="solo",
                action="core.transform.reshape",
                args={"value": "solo"},
                retry_policy=ActionRetryPolicy(max_attempts=1),
            )
        ],
    )

    dsl_worker, executor_worker = _workers(env, test_worker_factory)
    async with dsl_worker, executor_worker:
        handle = await _start(env, test_role, dsl, "overloaded")
        await handle.result()

    # One user attempt, yet two deferrals and then a run: deferrals used none.
    assert fake_actions.started == ["solo"]
    history = await _history(handle)
    converter = get_data_converter(compression_enabled=False)
    reasons: list[str] = []
    for event in history:
        if event.event_type is not EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            continue
        (result,) = await converter.decode(
            event.activity_task_completed_event_attributes.result.payloads
        )
        if (not_admitted := parse_not_admitted(result)) is not None:
            reasons.append(not_admitted.reason)
            assert (not_admitted.in_use, not_admitted.limit) == (-1, -1)
    assert reasons == [NotAdmittedReason.EXECUTOR_OVERLOADED] * 2
    # Durable backoff, not an in-activity wait.
    assert len(_timers(history)) == 2
    # Overload never opened the breaker: Redis was used for the admitted run.
    assert gate._breaker_open_until == 0.0
    store = await LeaseStore.create()
    assert await store.in_use(org_id, ConcurrencyResource.ACTIONS) == 0
