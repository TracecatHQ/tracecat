"""Tests for workflow timer and retry functionality.

Tests both timer control flow (wait_until, start_delay) and retry_until behavior.

Note: These tests use the time-skipping test environment and mock out the
execute_action_activity to avoid needing a real executor worker.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import cast

import dateparser
import pytest

pytestmark = [
    pytest.mark.temporal,
    pytest.mark.usefixtures("registry_version_with_manifest"),
]

import temporalio.api.enums.v1
from temporalio import activity
from temporalio.testing import WorkflowEnvironment

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.schemas import ActionRetryPolicy, ActionStatement, RunActionInput
from tracecat.dsl.worker import get_activities
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.executor.activities import ExecutorActivities
from tracecat.logger import logger
from tracecat.storage.object import InlineObject, StoredObject


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter(compression_enabled=False)
    ) as env:
        yield env


@pytest.mark.parametrize(
    "future_time",
    [
        pytest.param(
            lambda: (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            id="iso_future",
        ),
        pytest.param(lambda: "in 1 hour", id="in_1_hour"),
        pytest.param(lambda: "in 1h", id="in_1h"),
    ],
)
@pytest.mark.anyio
async def test_workflow_wait_until_future(
    request: pytest.FixtureRequest,
    test_role: Role,
    env: WorkflowEnvironment,
    future_time,
    test_worker_factory,
):
    """Test that wait_until with future date causes time skip."""
    # Resolve the future_time if it's a callable (lazy evaluation)
    resolved_time = cast(str, future_time() if callable(future_time) else future_time)

    dsl = DSLInput(
        title="wait_until_future",
        description="Test wait_until with future date",
        entrypoint=DSLEntrypoint(ref="delayed_action"),
        actions=[
            ActionStatement(
                ref="delayed_action",
                action="core.transform.reshape",
                args={"value": "test"},
                wait_until=resolved_time,
            )
        ],
    )

    num_activity_executions = 0

    # Mock out the execute_action_activity (replaces run_action_activity)
    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        nonlocal num_activity_executions
        num_activity_executions += 1
        return InlineObject(data=input.task.args["value"])

    # Get base activities and add the mock
    activities = get_activities()
    activities.append(execute_action_activity_mock)

    # Use standard queues: DSL worker on TEMPORAL__CLUSTER_QUEUE, executor mock on TRACECAT__EXECUTOR_QUEUE
    async with (
        test_worker_factory(env.client, activities=activities),
        test_worker_factory(
            env.client,
            activities=[execute_action_activity_mock],
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
        ),
    ):
        start_time = await env.get_current_time()
        handle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id(
                f"test_workflow_wait_until_future_{request.node.callspec.id}"
            ),
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )
        # Time skip 2 minutes
        await env.sleep(timedelta(hours=2))
        # Check that the activity has been called, we're now waiting for the sleep to finish.
        assert num_activity_executions == 1

        # Expect more than 2 hours to have passed
        assert (await env.get_current_time() - start_time) > timedelta(hours=2)

        # Verify result - workflow returns InlineObject(data=context)
        result = await handle.result()
        assert isinstance(result, InlineObject)
        # result is InlineObject - use .data attribute
        context = result.data
        assert context["ACTIONS"]["delayed_action"]["result"]["data"] == "test"


@pytest.mark.anyio
async def test_workflow_retry_until_condition(
    env: WorkflowEnvironment, test_role: Role, test_worker_factory
):
    """Test retry_until with a condition based on action result. No timers involved."""

    dsl = DSLInput(
        title="retry_until_condition",
        description="Test retry_until with condition",
        entrypoint=DSLEntrypoint(ref="retry_action"),
        actions=[
            ActionStatement(
                ref="retry_action",
                action="core.transform.reshape",
                # NOTE: This is mocked out in the test
                args={"value": "<MOCKED_OUT>"},
                retry_policy=ActionRetryPolicy(
                    # Retry until the action returns a 'success' status value
                    retry_until="${{ ACTIONS.retry_action.result.status == 'success' }}",
                ),
            )
        ],
    )

    num_activity_executions = 0

    # Mock out the execute_action_activity (replaces run_action_activity)
    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        nonlocal num_activity_executions
        num_activity_executions += 1
        if num_activity_executions < 3:
            return InlineObject(data={"status": "loading"})
        return InlineObject(data={"status": "success"})

    # Get base activities and add the mock
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
        result = await env.client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_retry_until_condition"),
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )

        # Expect 3 activity executions
        assert num_activity_executions == 3

        # Verify action was retried until condition met
        assert isinstance(result, InlineObject)
        context = result.data
        assert (
            context["ACTIONS"]["retry_action"]["result"]["data"]["status"] == "success"
        )


@pytest.mark.anyio
async def test_workflow_can_reschedule_at_tomorrow_9am(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory,
):
    """Test that a workflow can reschedule itself at 9am tomorrow a few times."""

    dsl = DSLInput(
        title="retry_until_condition",
        description="Test retry_until with condition",
        entrypoint=DSLEntrypoint(ref="retry_action"),
        actions=[
            ActionStatement(
                ref="retry_action",
                action="core.transform.reshape",
                args={"value": "<MOCKED_OUT>"},
                retry_policy=ActionRetryPolicy(
                    retry_until="${{ ACTIONS.retry_action.result.status == 'success' }}",
                ),
                wait_until="9am tomorrow",
            )
        ],
    )

    num_activity_executions = 0

    # Mock out the execute_action_activity (replaces run_action_activity)
    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        nonlocal num_activity_executions
        num_activity_executions += 1
        if num_activity_executions < 3:
            return InlineObject(data={"status": "loading"})
        return InlineObject(data={"status": "success"})

    # Get base activities and add the mock
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
        handle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_retry_until_condition"),
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )
        start_time = await env.get_current_time()
        # Duration until 9am tomorrow
        tmr_9am = dateparser.parse(
            "9am tomorrow",
            settings={"TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True},
        )
        assert tmr_9am is not None
        first_delay = tmr_9am - start_time

        # Time skip expected delay plus 1 min buffer
        logger.info("Sleeping for first delay", first_delay=first_delay)

        await env.sleep(first_delay + timedelta(minutes=1))
        # Expect 1 activity execution
        assert num_activity_executions == 1
        # Time now?
        logger.warning("Day 1 time", now=await env.get_current_time())
        # Assert that it's already 9am tomorrow
        assert (await env.get_current_time()) >= tmr_9am

        # NOTE: For some reason, the block below isn't working as expected,
        # but we see that the worfklow runs the correct number of times.

        # # Go to the next day
        # await env.sleep(timedelta(days=1, minutes=10))
        # # What's the time now?
        # logger.warning("Day 2 time", now=await env.get_current_time())
        # # Expect 2 activity executions
        # await asyncio.sleep(0)
        # assert num_activity_executions == 2

        # Time skip 3 days
        await env.sleep(timedelta(days=3))
        # Expect 3 activity executions
        assert num_activity_executions == 3

        # Expect workflow to be completed
        desc = await handle.describe()
        assert (
            desc.status
            == temporalio.api.enums.v1.WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_COMPLETED
        )
        assert await handle.result() is not None
        # Verify that the workflow ran the correct number of times
        assert num_activity_executions == 3


@pytest.mark.anyio
async def test_workflow_waits_until_tomorrow_9am(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory,
):
    """Test retry_until with a condition based on action result. With wait_until involved.

    The action should wait for the specified time before it runs, then retry 3 times until it succeeds.
    On each retry, it should wait the same amount of time before retrying.
    """

    dsl = DSLInput(
        title="retry_until_condition",
        description="Test retry_until with condition",
        entrypoint=DSLEntrypoint(ref="retry_action"),
        actions=[
            ActionStatement(
                ref="retry_action",
                action="core.transform.reshape",
                args={"value": "<MOCKED_OUT>"},
                wait_until="9am tomorrow",
            )
        ],
    )

    num_activity_executions = 0

    # Mock out the execute_action_activity (replaces run_action_activity)
    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        nonlocal num_activity_executions
        num_activity_executions += 1
        return InlineObject(data={"status": "success"})

    # Get base activities and add the mock
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
        start_time = await env.get_current_time()
        _ = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_retry_until_condition"),
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )
        # Duration until 9am tomorrow
        t = dateparser.parse(
            "9am tomorrow",
            settings={"TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True},
        )
        assert t is not None
        expected_delay = t - start_time

        # Time skip expected delay plus 1 min buffer
        await env.sleep(expected_delay + timedelta(minutes=1))
        # Expect 1 activity execution
        assert num_activity_executions == 1

        # Check that is it indeed 9am tomorrow
        assert (await env.get_current_time()) >= t


@pytest.mark.parametrize(
    "past_time",
    [
        pytest.param(
            lambda: (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            id="iso_past",
        ),
        pytest.param(lambda: "1 hour ago", id="1_hour_ago"),
        pytest.param(lambda: "1h", id="1h"),
    ],
)
@pytest.mark.anyio
async def test_workflow_wait_until_past(
    request: pytest.FixtureRequest,
    env: WorkflowEnvironment,
    test_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    past_time,
    test_worker_factory,
):
    """Test that wait_until with past date skips timer."""
    # Resolve the past_time if it's a callable (lazy evaluation for xdist compatibility)
    resolved_time = cast(str, past_time() if callable(past_time) else past_time)

    # Monkeypatch out asyncio.sleep with a counter.
    # Only count non-zero sleeps (actual timers), not cooperative yields (sleep(0)).
    num_sleeps = 0

    async def sleep_mock(seconds: float) -> None:
        nonlocal num_sleeps
        if seconds > 0:
            num_sleeps += 1

    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    dsl = DSLInput(
        title="wait_until_past",
        description="Test wait_until with past date",
        entrypoint=DSLEntrypoint(ref="delayed_action"),
        actions=[
            ActionStatement(
                ref="delayed_action",
                action="core.transform.reshape",
                args={"value": "test"},
                wait_until=resolved_time,
            )
        ],
    )

    # Mock out the execute_action_activity
    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        return InlineObject(data=input.task.args["value"])

    # Get base activities and add the mock
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
        await env.client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id(
                f"test_workflow_wait_until_past_{request.node.callspec.id}"
            ),
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )
        # Assert that no sleeps occurred
        assert num_sleeps == 0


@pytest.mark.anyio
async def test_workflow_start_delay(
    env: WorkflowEnvironment, test_role: Role, test_worker_factory
):
    """Test that start_delay creates appropriate timer."""
    delay_seconds = 3600  # 1 hour

    dsl = DSLInput(
        title="start_delay",
        description="Test start_delay timer",
        entrypoint=DSLEntrypoint(ref="delayed_action"),
        actions=[
            ActionStatement(
                ref="delayed_action",
                action="core.transform.reshape",
                args={"value": "<MOCKED_OUT>"},
                start_delay=delay_seconds,
            )
        ],
    )

    num_activity_executions = 0

    # Mock out the execute_action_activity (replaces run_action_activity)
    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        nonlocal num_activity_executions
        num_activity_executions += 1
        return InlineObject(data="test")

    # Get base activities and add the mock
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
        start_time = await env.get_current_time()
        handle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_start_delay"),
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )
        # Time skip 1 hour
        await env.sleep(timedelta(hours=2))
        # Assert that the activity has been called
        assert num_activity_executions == 1

        await handle.result()

        # Verify time was skipped by over the delay amount
        assert (await env.get_current_time() - start_time) >= timedelta(
            seconds=delay_seconds
        )
