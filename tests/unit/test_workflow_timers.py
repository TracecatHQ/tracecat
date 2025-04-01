"""Tests for workflow timer and retry functionality.

Tests both timer control flow (wait_until, start_delay) and retry_until behavior.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import dateparser
import pytest
import temporalio.api.enums.v1
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat.dsl._converter import pydantic_data_converter
from tracecat.dsl.action import DSLActivities
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.models import ActionRetryPolicy, ActionStatement, RunActionInput
from tracecat.dsl.worker import all_activities, new_sandbox_runner
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.logger import logger
from tracecat.types.auth import Role


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        yield env


@pytest.mark.parametrize(
    "future_time",
    [
        (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "in 1 hour",
        "in 1h",
    ],
)
@pytest.mark.anyio
async def test_workflow_wait_until_future(
    test_role: Role, env: WorkflowEnvironment, future_time: str
):
    """Test that wait_until with future date causes time skip."""

    dsl = DSLInput(
        title="wait_until_future",
        description="Test wait_until with future date",
        entrypoint=DSLEntrypoint(ref="delayed_action"),
        actions=[
            ActionStatement(
                ref="delayed_action",
                action="core.transform.reshape",
                args={"value": "test"},
                wait_until=future_time,
            )
        ],
    )

    num_activity_executions = 0

    # Mock out the activity to count executions
    @activity.defn(name="run_action_activity")
    async def run_action_activity_mock(input: RunActionInput, role: Role) -> str:
        nonlocal num_activity_executions
        num_activity_executions += 1
        return input.task.args["value"]

    # Mock out the activity to count executions
    activities = all_activities()
    activities.remove(DSLActivities.run_action_activity)
    activities.append(run_action_activity_mock)

    async with Worker(
        env.client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=activities,
        workflow_runner=new_sandbox_runner(),
    ):
        start_time = await env.get_current_time()
        handle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_wait_until_future"),
            task_queue="test-queue",
        )
        # Time skip 2 minutes
        await env.sleep(timedelta(hours=2))
        # Check that the activity has been called, we're now waiting for the sleep to finish.
        assert num_activity_executions == 1

        # Expect more than 2 hours to have passed
        assert (await env.get_current_time() - start_time) > timedelta(hours=2)

        # Verify result
        result = await handle.result()
        assert result["ACTIONS"]["delayed_action"]["result"] == "test"


@pytest.mark.anyio
async def test_workflow_retry_until_condition(
    env: WorkflowEnvironment, test_role: Role
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

    # Mock out the activity to count executions
    @activity.defn(name="run_action_activity")
    async def run_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> dict[str, str]:
        nonlocal num_activity_executions
        num_activity_executions += 1
        if num_activity_executions < 3:
            return {"status": "loading"}
        return {"status": "success"}

    # Mock out the activity to count executions
    activities = all_activities()
    activities.remove(DSLActivities.run_action_activity)
    activities.append(run_action_activity_mock)

    async with Worker(
        env.client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=activities,
        workflow_runner=new_sandbox_runner(),
    ):
        result = await env.client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_retry_until_condition"),
            task_queue="test-queue",
        )

        # Expect 3 activity executions
        assert num_activity_executions == 3

        # Verify action was retried until condition met
        assert result["ACTIONS"]["retry_action"]["result"]["status"] == "success"


@pytest.mark.anyio
async def test_workflow_can_reschedule_at_tomorrow_9am(
    env: WorkflowEnvironment,
    test_role: Role,
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

    # Mock out the activity to count executions
    @activity.defn(name="run_action_activity")
    async def run_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> dict[str, str]:
        nonlocal num_activity_executions
        num_activity_executions += 1
        if num_activity_executions < 3:
            return {"status": "loading"}
        return {"status": "success"}

    # Mock out the activity to count executions
    activities = all_activities()
    activities.remove(DSLActivities.run_action_activity)
    activities.append(run_action_activity_mock)

    async with Worker(
        env.client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=activities,
        workflow_runner=new_sandbox_runner(),
    ):
        handle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_retry_until_condition"),
            task_queue="test-queue",
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

    # Mock out the activity to count executions
    @activity.defn(name="run_action_activity")
    async def run_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> dict[str, str]:
        nonlocal num_activity_executions
        num_activity_executions += 1
        return {"status": "success"}

    # Mock out the activity to count executions
    activities = all_activities()
    activities.remove(DSLActivities.run_action_activity)
    activities.append(run_action_activity_mock)

    async with Worker(
        env.client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=activities,
        workflow_runner=new_sandbox_runner(),
    ):
        start_time = await env.get_current_time()
        _ = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_retry_until_condition"),
            task_queue="test-queue",
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
    "wait_time,expected_delay",
    [
        ("in 5 minutes", timedelta(minutes=5)),
        ("in 1 hour", timedelta(hours=1)),
    ],
)
@pytest.mark.anyio
async def test_workflow_retry_until_condition_with_wait_until(
    env: WorkflowEnvironment,
    test_role: Role,
    wait_time: str,
    expected_delay: timedelta,
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
                retry_policy=ActionRetryPolicy(
                    retry_until="${{ ACTIONS.retry_action.result.status == 'success' }}",
                ),
                wait_until=wait_time,
            )
        ],
    )

    num_activity_executions = 0

    # Mock out the activity to count executions
    @activity.defn(name="run_action_activity")
    async def run_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> dict[str, str]:
        nonlocal num_activity_executions
        num_activity_executions += 1
        if num_activity_executions < 3:
            return {"status": "loading"}
        return {"status": "success"}

    # Mock out the activity to count executions
    activities = all_activities()
    activities.remove(DSLActivities.run_action_activity)
    activities.append(run_action_activity_mock)

    async with Worker(
        env.client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=activities,
        workflow_runner=new_sandbox_runner(),
    ):
        start_time = await env.get_current_time()
        handle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_retry_until_condition"),
            task_queue="test-queue",
        )
        # Time skip expected delay plus 1 min buffer
        await env.sleep(expected_delay + timedelta(minutes=1))
        # Expect 1 activity execution
        assert num_activity_executions == 1

        # Skip expected delay again
        await env.sleep(expected_delay + timedelta(minutes=1))
        # Expect 2 activity executions
        assert num_activity_executions == 2

        # Skip longer time to ensure final execution
        await env.sleep(expected_delay * 2)

        # Expect 3 activity executions
        assert num_activity_executions == 3

        # Expect more than 2x the expected delay has passed
        assert (await env.get_current_time() - start_time) > (expected_delay * 2)

        # Verify action was retried until condition met
        result = await handle.result()
        assert result["ACTIONS"]["retry_action"]["result"]["status"] == "success"


@pytest.mark.parametrize(
    "past_time",
    [
        (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "1 hour ago",
        "1h",
    ],
)
@pytest.mark.anyio
async def test_workflow_wait_until_past(
    env: WorkflowEnvironment,
    test_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    past_time: str,
):
    """Test that wait_until with past date skips timer."""

    # Monkeypatch out  asyncio.sleep with a counter
    num_sleeps = 0

    async def sleep_mock(seconds: float) -> NoReturn:
        nonlocal num_sleeps
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
                wait_until=past_time,
            )
        ],
    )

    async with Worker(
        env.client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=all_activities(),
        workflow_runner=new_sandbox_runner(),
    ):
        await env.client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_wait_until_past"),
            task_queue="test-queue",
        )
        # Assert that no sleeps occurred
        assert num_sleeps == 0


@pytest.mark.anyio
async def test_workflow_start_delay(env: WorkflowEnvironment, test_role: Role):
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

    # Mock out the activity to count executions
    @activity.defn(name="run_action_activity")
    async def run_action_activity_mock(input: RunActionInput, role: Role) -> str:
        nonlocal num_activity_executions
        num_activity_executions += 1
        return "test"

    # Mock out the activity to count executions
    activities = all_activities()
    activities.remove(DSLActivities.run_action_activity)
    activities.append(run_action_activity_mock)

    async with Worker(
        env.client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=activities,
        workflow_runner=new_sandbox_runner(),
    ):
        start_time = await env.get_current_time()
        handle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_start_delay"),
            task_queue="test-queue",
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


### TODO: Finish the tests below later


@pytest.mark.skip
@pytest.mark.anyio
async def test_workflow_wait_until_precedence(
    env: WorkflowEnvironment, test_role: Role
):
    """Test that wait_until takes precedence over start_delay."""
    future_time = datetime.now(UTC) + timedelta(hours=1)

    dsl = DSLInput(
        title="wait_until_precedence",
        description="Test wait_until precedence over start_delay",
        entrypoint=DSLEntrypoint(ref="delayed_action"),
        actions=[
            ActionStatement(
                ref="delayed_action",
                action="core.transform.reshape",
                args={"value": "test"},
                wait_until=future_time.isoformat(),
                start_delay=30,  # Should be ignored in favor of wait_until
            )
        ],
    )

    client = env.client
    async with Worker(
        client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=[DSLActivities.parse_wait_until_activity],
    ):
        start_time = datetime.now(UTC)
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_wait_until_precedence"),
            task_queue="test-queue",
        )
        end_time = datetime.now(UTC)

        # Verify wait_until time was used instead of start_delay
        assert end_time - start_time >= timedelta(hours=1)
        assert result["ACTIONS"]["delayed_action"]["result"] == "test"


@pytest.mark.skip
@pytest.mark.anyio
async def test_workflow_invalid_wait_until(env: WorkflowEnvironment, test_role: Role):
    """Test that invalid wait_until date format raises error."""
    dsl = DSLInput(
        title="invalid_wait_until",
        description="Test invalid wait_until format",
        entrypoint=DSLEntrypoint(ref="delayed_action"),
        actions=[
            ActionStatement(
                ref="delayed_action",
                action="core.transform.reshape",
                args={"value": "test"},
                wait_until="invalid-date-format",
            )
        ],
    )

    client = env.client
    async with Worker(
        client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=[DSLActivities.parse_wait_until_activity],
    ):
        with pytest.raises(ApplicationError, match="Invalid wait until date"):
            await client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
                id=generate_test_exec_id("test_workflow_invalid_wait_until"),
                task_queue="test-queue",
            )


@pytest.mark.skip
@pytest.mark.anyio
async def test_workflow_retry_until_max_attempts(
    env: WorkflowEnvironment, test_role: Role
):
    """Test retry_until with max attempts exceeded."""
    dsl = DSLInput(
        title="retry_until_max_attempts",
        description="Test retry_until with max attempts",
        entrypoint=DSLEntrypoint(ref="retry_action"),
        actions=[
            ActionStatement(
                ref="retry_action",
                action="core.transform.reshape",
                args={"value": "${{ ACTIONS.retry_action.attempt or 1 }}"},
                retry_policy=ActionRetryPolicy(
                    retry_until="${{ ACTIONS.retry_action.result >= 10 }}",  # Impossible condition
                    max_attempts=3,
                    timeout=10,
                ),
            )
        ],
    )

    client = env.client
    async with Worker(
        client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=[DSLActivities.parse_wait_until_activity],
    ):
        with pytest.raises(ApplicationError, match="Maximum attempts exceeded"):
            await client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
                id=generate_test_exec_id("test_workflow_retry_until_max_attempts"),
                task_queue="test-queue",
            )


@pytest.mark.skip
@pytest.mark.anyio
async def test_workflow_retry_until_timeout(env: WorkflowEnvironment, test_role: Role):
    """Test retry_until with timeout exceeded."""
    dsl = DSLInput(
        title="retry_until_timeout",
        description="Test retry_until with timeout",
        entrypoint=DSLEntrypoint(ref="retry_action"),
        actions=[
            ActionStatement(
                ref="retry_action",
                action="core.transform.reshape",
                args={"value": "${{ ACTIONS.retry_action.attempt or 1 }}"},
                retry_policy=ActionRetryPolicy(
                    retry_until="${{ ACTIONS.retry_action.result >= 10 }}",
                    max_attempts=10,
                    timeout=1,  # Short timeout to trigger error
                ),
            )
        ],
    )

    client = env.client
    async with Worker(
        client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=[DSLActivities.parse_wait_until_activity],
    ):
        with pytest.raises(ApplicationError, match="Activity timeout"):
            await client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
                id=generate_test_exec_id("test_workflow_retry_until_timeout"),
                task_queue="test-queue",
            )


@pytest.mark.skip
@pytest.mark.anyio
async def test_workflow_multiple_timed_actions(
    env: WorkflowEnvironment, test_role: Role
):
    """Test multiple actions with different timing behaviors."""
    future_time = datetime.now(UTC) + timedelta(minutes=30)

    dsl = DSLInput(
        title="multiple_timed_actions",
        description="Test multiple timed actions",
        entrypoint=DSLEntrypoint(ref="action1"),
        actions=[
            ActionStatement(
                ref="action1",
                action="core.transform.reshape",
                args={"value": "first"},
                start_delay=60,
            ),
            ActionStatement(
                ref="action2",
                action="core.transform.reshape",
                args={"value": "second"},
                wait_until=future_time.isoformat(),
                depends_on=["action1"],
            ),
            ActionStatement(
                ref="action3",
                action="core.transform.reshape",
                args={"value": "${{ ACTIONS.action3.attempt or 1 }}"},
                retry_policy=ActionRetryPolicy(
                    retry_until="${{ ACTIONS.action3.result >= 3 }}",
                    max_attempts=5,
                    timeout=10,
                ),
                depends_on=["action2"],
            ),
        ],
    )

    client = env.client
    async with Worker(
        client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=[DSLActivities.parse_wait_until_activity],
    ):
        start_time = datetime.now(UTC)
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_multiple_timed_actions"),
            task_queue="test-queue",
        )
        end_time = datetime.now(UTC)

        # Verify timing and results
        assert end_time - start_time >= timedelta(minutes=30)
        assert result["ACTIONS"]["action1"]["result"] == "first"
        assert result["ACTIONS"]["action2"]["result"] == "second"
        assert result["ACTIONS"]["action3"]["result"] == 3


@pytest.mark.skip
@pytest.mark.anyio
async def test_workflow_retry_until_time_condition(
    env: WorkflowEnvironment, test_role: Role
):
    """Test retry_until with time-based condition."""
    target_time = datetime.now(UTC) + timedelta(minutes=5)

    dsl = DSLInput(
        title="retry_until_time",
        description="Test retry_until with time condition",
        entrypoint=DSLEntrypoint(ref="retry_action"),
        actions=[
            ActionStatement(
                ref="retry_action",
                action="core.transform.reshape",
                args={"value": datetime.now(UTC).isoformat()},
                retry_policy=ActionRetryPolicy(
                    retry_until=f"${{ datetime.fromisoformat(ACTIONS.retry_action.result) >= datetime.fromisoformat('{target_time.isoformat()}') }}",
                    max_attempts=10,
                    timeout=300,
                ),
            )
        ],
    )

    client = env.client
    async with Worker(
        client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=[DSLActivities.parse_wait_until_activity],
    ):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=generate_test_exec_id("test_workflow_retry_until_time_condition"),
            task_queue="test-queue",
        )

        # Verify the final result time is after target time
        final_time = datetime.fromisoformat(result["ACTIONS"]["retry_action"]["result"])
        assert final_time >= target_time


@pytest.mark.skip
@pytest.mark.anyio
async def test_workflow_invalid_retry_until_expression(
    env: WorkflowEnvironment, test_role: Role
):
    """Test that invalid retry_until expression raises error."""
    dsl = DSLInput(
        title="invalid_retry_until",
        description="Test invalid retry_until expression",
        entrypoint=DSLEntrypoint(ref="retry_action"),
        actions=[
            ActionStatement(
                ref="retry_action",
                action="core.transform.reshape",
                args={"value": "test"},
                retry_policy=ActionRetryPolicy(
                    retry_until="invalid {{ expression",
                    max_attempts=3,
                    timeout=10,
                ),
            )
        ],
    )

    client = env.client
    async with Worker(
        client,
        task_queue="test-queue",
        workflows=[DSLWorkflow],
        activities=[DSLActivities.parse_wait_until_activity],
    ):
        with pytest.raises(ApplicationError, match="Invalid retry_until expression"):
            await client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
                id=generate_test_exec_id(
                    "test_workflow_invalid_retry_until_expression"
                ),
                task_queue="test-queue",
            )
