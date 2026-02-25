"""Temporal e2e tests for workflow concurrency tier limits.

These tests exercise the real tier-limit activities and Redis semaphore behavior
through running workflows, while mocking action execution for deterministic
coordination.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import timedelta

import pytest
from sqlalchemy.dialects.postgresql import insert as pg_insert
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Organization, OrganizationTier, Tier
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.schemas import (
    ActionRetryPolicy,
    ActionStatement,
    GatherArgs,
    RunActionInput,
    ScatterArgs,
)
from tracecat.dsl.worker import get_activities
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.executor.activities import ExecutorActivities
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.redis import client as redis_client_module
from tracecat.redis.client import get_redis_client
from tracecat.storage.object import InlineObject, StoredObject

pytestmark = [
    pytest.mark.temporal,
    pytest.mark.usefixtures("registry_version_with_manifest"),
]


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter(compression_enabled=False)
    ) as workflow_env:
        yield workflow_env


@pytest.fixture
async def concurrency_limits_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[None, None]:
    """Enable tier-limit enforcement and isolate Redis for this test session.

    This fixture enables the workflow concurrency feature flags and sets timing
    constants to short values so backoff and permit wait behavior can be exercised
    quickly in tests. It also points Redis client configuration at the test
    environment before each test and tears down the shared connection before and
    after execution.
    """
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(
        config,
        "TRACECAT__FEATURE_FLAGS",
        {FeatureFlag.WORKFLOW_CONCURRENCY_LIMITS},
    )
    monkeypatch.setattr(config, "TRACECAT__WORKFLOW_PERMIT_BACKOFF_BASE_SECONDS", 0.02)
    monkeypatch.setattr(config, "TRACECAT__WORKFLOW_PERMIT_BACKOFF_MAX_SECONDS", 0.1)
    monkeypatch.setattr(config, "TRACECAT__WORKFLOW_PERMIT_MAX_WAIT_SECONDS", 30)
    monkeypatch.setattr(config, "TRACECAT__ACTION_PERMIT_MAX_WAIT_SECONDS", 30)
    monkeypatch.setattr(config, "TRACECAT__WORKFLOW_PERMIT_HEARTBEAT_SECONDS", 10.0)
    monkeypatch.setattr(redis_client_module, "REDIS_URL", os.environ["REDIS_URL"])

    redis_wrapper = await get_redis_client()
    await redis_wrapper.close()
    yield
    await redis_wrapper.close()


def _workflow_semaphore_key(org_id: uuid.UUID) -> str:
    """Build the Redis key used for workflow permit tracking for an org."""
    return f"tier:org:{org_id}:semaphore"


def _action_semaphore_key(org_id: uuid.UUID) -> str:
    """Build the Redis key used for action permit tracking for an org."""
    return f"tier:org:{org_id}:action-semaphore"


def _build_single_action_dsl(
    *,
    title: str,
    ref: str,
    start_delay_seconds: float = 0.0,
    retry_until: str | None = None,
) -> DSLInput:
    """Build a single-action DSL used by concurrency tests.

    The returned DSL keeps the action name and reference stable, while allowing the
    caller to vary optional retry timing and start delay behavior per test.
    """
    retry_policy = (
        ActionRetryPolicy(retry_until=retry_until)
        if retry_until is not None
        else ActionRetryPolicy()
    )
    return DSLInput(
        title=title,
        description=title,
        entrypoint=DSLEntrypoint(ref=ref),
        actions=[
            ActionStatement(
                ref=ref,
                action="core.transform.reshape",
                args={"ref": ref},
                start_delay=start_delay_seconds,
                retry_policy=retry_policy,
            )
        ],
    )


async def _configure_org_limits(
    org_id: uuid.UUID,
    *,
    max_concurrent_workflows: int | None = None,
    max_concurrent_actions: int | None = None,
    max_action_executions_per_workflow: int | None = None,
) -> None:
    """Upsert an organization tier configuration with deterministic e2e limits."""
    tier_id = uuid.uuid5(org_id, "temporal-tier")
    tier_values = {
        "display_name": f"Temporal Tier {org_id.hex[:8]}",
        "max_concurrent_workflows": max_concurrent_workflows,
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


async def _start_batch(
    *,
    env: WorkflowEnvironment,
    role: Role,
    dsl: DSLInput,
    workflow_name_prefix: str,
    size: int,
) -> list[WorkflowHandle]:
    """Start a batch of identical workflows using the same role and DSL.

    Returns:
        A list of workflow handles, one per started workflow, so callers can await
        completion and assert post-run semaphore state.
    """
    handles: list[WorkflowHandle] = []
    for i in range(size):
        handle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id(f"{workflow_name_prefix}_{i}_{uuid.uuid4().hex[:6]}"),
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )
        handles.append(handle)
    return handles


def _role_for_org(base_role: Role, org_id: uuid.UUID) -> Role:
    """Return a role copy scoped to the requested organization."""
    return base_role.model_copy(update={"organization_id": org_id})


@pytest.mark.anyio
async def test_workflow_concurrency_batch_never_exceeds_cap(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    concurrency_limits_enabled: None,
) -> None:
    """Verify workflow permit acquisition caps concurrent active workflows.

    The test starts more workflows than the configured permit cap, mocks action
    execution to park each workflow on permit hold, then checks that active
    workflow permits never grow beyond the configured limit. It also observes the
    Redis semaphore directly to verify it reflects the same limit and is cleaned up
    after all workflows complete.
    """
    cap = 3
    batch_size = 8
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_workflows=cap)

    redis_wrapper = await get_redis_client()
    redis_client = await redis_wrapper._get_client()
    semaphore_key = _workflow_semaphore_key(org_id)
    await redis_client.delete(semaphore_key)

    in_flight_actions = 0
    max_in_flight_actions = 0
    max_workflow_permits = 0
    lock = asyncio.Lock()
    reached_cap = asyncio.Event()
    release_actions = asyncio.Event()
    stop_monitor = asyncio.Event()

    async def monitor_workflow_permits() -> None:
        nonlocal max_workflow_permits
        while not stop_monitor.is_set():
            count = int(await redis_client.zcard(semaphore_key))
            max_workflow_permits = max(max_workflow_permits, count)
            await asyncio.sleep(0.01)

    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        del input, role
        nonlocal in_flight_actions, max_in_flight_actions
        async with lock:
            in_flight_actions += 1
            max_in_flight_actions = max(max_in_flight_actions, in_flight_actions)
            if in_flight_actions >= cap:
                reached_cap.set()
        try:
            await release_actions.wait()
            return InlineObject(data={"ok": True})
        finally:
            async with lock:
                in_flight_actions -= 1

    dsl = _build_single_action_dsl(title="workflow-cap-batch", ref="task")
    activities = get_activities()
    activities.append(execute_action_activity_mock)

    monitor_task = asyncio.create_task(monitor_workflow_permits())

    async with (
        test_worker_factory(env.client, activities=activities),
        test_worker_factory(
            env.client,
            activities=[execute_action_activity_mock],
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
        ),
    ):
        handles = await _start_batch(
            env=env,
            role=test_role,
            dsl=dsl,
            workflow_name_prefix="workflow_cap_batch",
            size=batch_size,
        )

        await asyncio.wait_for(reached_cap.wait(), timeout=10)
        assert in_flight_actions == cap
        assert int(await redis_client.zcard(semaphore_key)) == cap

        release_actions.set()
        for handle in handles:
            await handle.result()

    stop_monitor.set()
    await asyncio.gather(monitor_task, return_exceptions=True)

    assert max_in_flight_actions <= cap
    assert max_workflow_permits <= cap
    assert int(await redis_client.zcard(semaphore_key)) == 0


@pytest.mark.anyio
async def test_action_concurrency_never_exceeds_cap(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    concurrency_limits_enabled: None,
) -> None:
    """Verify action permit cap is enforced for a workflow batch.

    We configure an organization-level action concurrency limit, launch more workflows
    than that cap, and hold mocked action executions open. The test asserts that
    only the capped number of actions can hold permits simultaneously, while
    remaining workflows wait.
    """
    cap = 2
    batch_size = 7
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_actions=cap)

    redis_wrapper = await get_redis_client()
    redis_client = await redis_wrapper._get_client()
    semaphore_key = _action_semaphore_key(org_id)
    await redis_client.delete(semaphore_key)

    in_flight_actions = 0
    max_in_flight_actions = 0
    max_action_permits = 0
    lock = asyncio.Lock()
    reached_cap = asyncio.Event()
    release_actions = asyncio.Event()
    stop_monitor = asyncio.Event()

    async def monitor_action_permits() -> None:
        nonlocal max_action_permits
        while not stop_monitor.is_set():
            count = int(await redis_client.zcard(semaphore_key))
            max_action_permits = max(max_action_permits, count)
            await asyncio.sleep(0.01)

    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        del input, role
        nonlocal in_flight_actions, max_in_flight_actions
        async with lock:
            in_flight_actions += 1
            max_in_flight_actions = max(max_in_flight_actions, in_flight_actions)
            if in_flight_actions >= cap:
                reached_cap.set()
        try:
            await release_actions.wait()
            return InlineObject(data={"ok": True})
        finally:
            async with lock:
                in_flight_actions -= 1

    dsl = _build_single_action_dsl(title="action-cap-batch", ref="task")
    activities = get_activities()
    activities.append(execute_action_activity_mock)

    monitor_task = asyncio.create_task(monitor_action_permits())

    async with (
        test_worker_factory(env.client, activities=activities),
        test_worker_factory(
            env.client,
            activities=[execute_action_activity_mock],
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
        ),
    ):
        handles = await _start_batch(
            env=env,
            role=test_role,
            dsl=dsl,
            workflow_name_prefix="action_cap_batch",
            size=batch_size,
        )

        await asyncio.wait_for(reached_cap.wait(), timeout=10)
        assert in_flight_actions == cap
        assert int(await redis_client.zcard(semaphore_key)) == cap

        release_actions.set()
        for handle in handles:
            await handle.result()

    stop_monitor.set()
    await asyncio.gather(monitor_task, return_exceptions=True)

    assert max_in_flight_actions <= cap
    assert max_action_permits <= cap
    assert int(await redis_client.zcard(semaphore_key)) == 0


@pytest.mark.anyio
async def test_scatter_gather_respects_action_concurrency_cap(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    concurrency_limits_enabled: None,
) -> None:
    """Ensure scatter branch actions obey org action concurrency cap.

    We execute a single workflow with a scatter fan-out larger than the cap and
    verify that only `cap` branch actions run concurrently while gather completes
    after release.
    """
    cap = 2
    scatter_width = 6
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_actions=cap)

    redis_wrapper = await get_redis_client()
    redis_client = await redis_wrapper._get_client()
    semaphore_key = _action_semaphore_key(org_id)
    await redis_client.delete(semaphore_key)

    in_flight_actions = 0
    max_in_flight_actions = 0
    max_action_permits = 0
    lock = asyncio.Lock()
    reached_cap = asyncio.Event()
    release_actions = asyncio.Event()
    stop_monitor = asyncio.Event()

    async def monitor_action_permits() -> None:
        nonlocal max_action_permits
        while not stop_monitor.is_set():
            count = int(await redis_client.zcard(semaphore_key))
            max_action_permits = max(max_action_permits, count)
            await asyncio.sleep(0.01)

    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        del role
        nonlocal in_flight_actions, max_in_flight_actions
        if input.task.ref != "work":
            return InlineObject(data={"ok": True})
        async with lock:
            in_flight_actions += 1
            max_in_flight_actions = max(max_in_flight_actions, in_flight_actions)
            if in_flight_actions >= cap:
                reached_cap.set()
        try:
            await release_actions.wait()
            return InlineObject(data=input.task.args["value"])
        finally:
            async with lock:
                in_flight_actions -= 1

    dsl = DSLInput(
        title="scatter-gather-cap",
        description="scatter/gather action cap",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                args=ScatterArgs(collection=list(range(scatter_width))).model_dump(),
            ),
            ActionStatement(
                ref="work",
                action="core.transform.reshape",
                depends_on=["scatter"],
                args={"value": "${{ ACTIONS.scatter.result }}"},
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["work"],
                args=GatherArgs(items="${{ ACTIONS.work.result }}").model_dump(),
            ),
        ],
    )
    activities = get_activities()
    activities.append(execute_action_activity_mock)

    monitor_task = asyncio.create_task(monitor_action_permits())

    async with (
        test_worker_factory(env.client, activities=activities),
        test_worker_factory(
            env.client,
            activities=[execute_action_activity_mock],
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
        ),
    ):
        handle: WorkflowHandle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id(f"scatter_gather_cap_{uuid.uuid4().hex[:6]}"),
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )

        await asyncio.wait_for(reached_cap.wait(), timeout=10)
        assert in_flight_actions == cap
        assert int(await redis_client.zcard(semaphore_key)) == cap

        release_actions.set()
        await handle.result()

    stop_monitor.set()
    await asyncio.gather(monitor_task, return_exceptions=True)

    assert max_in_flight_actions <= cap
    assert max_action_permits <= cap
    assert int(await redis_client.zcard(semaphore_key)) == 0


@pytest.mark.anyio
async def test_start_delay_does_not_consume_action_permit(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    concurrency_limits_enabled: None,
) -> None:
    """Show delayed actions do not consume action permits prematurely.

    A workflow with a long delayed action is started alongside an immediate action.
    The immediate workflow should acquire and hold the only available permit first;
    the delayed workflow should remain unstarted until its start delay passes and the
    permit is released.
    """
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_concurrent_actions=1)

    redis_wrapper = await get_redis_client()
    redis_client = await redis_wrapper._get_client()
    semaphore_key = _action_semaphore_key(org_id)
    await redis_client.delete(semaphore_key)

    immediate_started = asyncio.Event()
    delayed_started = asyncio.Event()
    release_immediate = asyncio.Event()

    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        del role
        if input.task.ref == "immediate_action":
            immediate_started.set()
            await release_immediate.wait()
        else:
            delayed_started.set()
        return InlineObject(data={"ref": input.task.ref})

    delayed_dsl = _build_single_action_dsl(
        title="delayed-action",
        ref="delayed_action",
        start_delay_seconds=3600,
    )
    immediate_dsl = _build_single_action_dsl(
        title="immediate-action",
        ref="immediate_action",
    )
    activities = get_activities()
    activities.append(execute_action_activity_mock)

    async with (
        test_worker_factory(env.client, activities=activities),
        test_worker_factory(
            env.client,
            activities=[execute_action_activity_mock],
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
        ),
    ):
        delayed_handle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=delayed_dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id(f"delayed_{uuid.uuid4().hex[:6]}"),
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )
        immediate_handle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=immediate_dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id(f"immediate_{uuid.uuid4().hex[:6]}"),
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )

        await asyncio.wait_for(immediate_started.wait(), timeout=10)
        assert delayed_started.is_set() is False
        assert int(await redis_client.zcard(semaphore_key)) == 1

        release_immediate.set()
        await immediate_handle.result()

        await env.sleep(timedelta(hours=2))
        await delayed_handle.result()
        assert delayed_started.is_set()

    assert int(await redis_client.zcard(semaphore_key)) == 0


@pytest.mark.anyio
async def test_retry_until_iterations_count_toward_action_execution_limit_e2e(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    concurrency_limits_enabled: None,
) -> None:
    """Confirm retry loops consume the action execution budget and fail at the cap.

    The mocked action returns a non-terminal status repeatedly while the DSL's
    retry policy evaluates a success condition. The test asserts the workflow fails
    with the expected limit error and that the action was executed exactly at the
    configured maximum attempts.
    """
    org_id = test_role.organization_id
    assert org_id is not None
    await _configure_org_limits(org_id, max_action_executions_per_workflow=2)

    num_activity_executions = 0

    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        del input, role
        nonlocal num_activity_executions
        num_activity_executions += 1
        return InlineObject(data={"status": "loading"})

    dsl = _build_single_action_dsl(
        title="retry-until-limit",
        ref="retry_action",
        retry_until="${{ ACTIONS.retry_action.result.status == 'success' }}",
    )
    activities = get_activities()
    activities.append(execute_action_activity_mock)

    async with (
        test_worker_factory(env.client, activities=activities),
        test_worker_factory(
            env.client,
            activities=[execute_action_activity_mock],
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
        ),
    ):
        with pytest.raises(WorkflowFailureError) as exc_info:
            await env.client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
                id=generate_test_exec_id(f"retry_limit_{uuid.uuid4().hex[:6]}"),
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
            )
        assert isinstance(exc_info.value.cause, ApplicationError)
        assert "Action execution limit exceeded" in str(exc_info.value.cause)

    assert num_activity_executions == 2


@pytest.mark.anyio
async def test_concurrency_caps_are_isolated_per_organization(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    concurrency_limits_enabled: None,
) -> None:
    """Validate semaphore namespaces are isolated by organization.

    Two org identifiers are configured with different action limits (1 vs 2) so we
    can verify each org's semaphore enforces its own cap independently. We launch
    enough workflows per org to saturate each cap, confirm the per-org permit counts
    match their respective limits, and verify both semaphores are drained after
    execution finishes.
    """
    cap_a = 1
    cap_b = 2
    batch_size_a = 3
    batch_size_b = 5
    org_a = test_role.organization_id
    assert org_a is not None
    org_b = uuid.uuid5(org_a, "temporal-concurrency-org-b")
    role_b = _role_for_org(test_role, org_b)

    await _configure_org_limits(org_a, max_concurrent_actions=cap_a)
    await _configure_org_limits(org_b, max_concurrent_actions=cap_b)

    redis_wrapper = await get_redis_client()
    redis_client = await redis_wrapper._get_client()
    key_a = _action_semaphore_key(org_a)
    key_b = _action_semaphore_key(org_b)
    await redis_client.delete(key_a)
    await redis_client.delete(key_b)

    in_flight_a = 0
    in_flight_b = 0
    max_in_flight_a = 0
    max_in_flight_b = 0
    lock = asyncio.Lock()
    a_reached_cap = asyncio.Event()
    b_reached_cap = asyncio.Event()
    release_actions = asyncio.Event()

    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        del input
        nonlocal in_flight_a, in_flight_b, max_in_flight_a, max_in_flight_b
        is_org_a = role.organization_id == org_a
        async with lock:
            if is_org_a:
                in_flight_a += 1
                max_in_flight_a = max(max_in_flight_a, in_flight_a)
                if in_flight_a >= cap_a:
                    a_reached_cap.set()
            else:
                in_flight_b += 1
                max_in_flight_b = max(max_in_flight_b, in_flight_b)
                if in_flight_b >= cap_b:
                    b_reached_cap.set()
        try:
            await release_actions.wait()
            return InlineObject(data={"ok": True})
        finally:
            async with lock:
                if is_org_a:
                    in_flight_a -= 1
                else:
                    in_flight_b -= 1

    dsl = _build_single_action_dsl(title="cross-org", ref="task")
    activities = get_activities()
    activities.append(execute_action_activity_mock)

    async with (
        test_worker_factory(env.client, activities=activities),
        test_worker_factory(
            env.client,
            activities=[execute_action_activity_mock],
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
        ),
    ):
        handles_a = await _start_batch(
            env=env,
            role=test_role,
            dsl=dsl,
            workflow_name_prefix="cross_org_a",
            size=batch_size_a,
        )
        handles_b = await _start_batch(
            env=env,
            role=role_b,
            dsl=dsl,
            workflow_name_prefix="cross_org_b",
            size=batch_size_b,
        )

        await asyncio.wait_for(a_reached_cap.wait(), timeout=10)
        await asyncio.wait_for(b_reached_cap.wait(), timeout=10)
        assert int(await redis_client.zcard(key_a)) == cap_a
        assert int(await redis_client.zcard(key_b)) == cap_b

        release_actions.set()
        for handle in handles_a + handles_b:
            await handle.result()

    assert max_in_flight_a <= cap_a
    assert max_in_flight_b <= cap_b
    assert int(await redis_client.zcard(key_a)) == 0
    assert int(await redis_client.zcard(key_b)) == 0
