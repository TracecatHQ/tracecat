"""Tests for scatter interval feature.

This module tests the ability to stagger scatter tasks with configurable intervals.
When a scatter operation has an `interval` parameter, each scattered task is delayed
by `index * interval` seconds, allowing for rate-limiting and controlled parallel execution.
"""

import os
import time
from collections.abc import Callable

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat.dsl.common import RETRY_POLICIES, DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.models import ActionStatement, GatherArgs, ScatterArgs
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.types.auth import Role


@pytest.mark.anyio
@pytest.mark.integration
async def test_scatter_with_interval_basic(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
):
    """Test that scatter with interval staggers task execution.

    This test verifies that when an interval is specified, each scattered task
    is delayed by the appropriate amount (0s, interval, 2*interval, 3*interval).
    """
    test_name = f"{test_scatter_with_interval_basic.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    # Use a small interval for testing (0.5 seconds)
    interval = 0.5
    collection_size = 4

    dsl = DSLInput(
        title="Scatter with interval",
        description="Test scatter tasks are staggered by interval",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                args=ScatterArgs(
                    collection="${{ [1, 2, 3, 4] }}",
                    interval=interval,
                ).model_dump(),
            ),
            ActionStatement(
                ref="record_time",
                action="core.transform.reshape",
                depends_on=["scatter"],
                args={
                    "value": {
                        "item": "${{ ACTIONS.scatter.result }}",
                        # We can't directly capture time in the workflow,
                        # but we can verify execution through the result
                    }
                },
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["record_time"],
                args=GatherArgs(items="${{ ACTIONS.record_time.result }}").model_dump(),
            ),
        ],
    )

    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    start_time = time.time()

    async with test_worker_factory(temporal_client):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    end_time = time.time()
    elapsed = end_time - start_time

    # Verify results are correct
    gathered_results = result["ACTIONS"]["gather"]["result"]
    assert len(gathered_results) == collection_size
    assert all("item" in r for r in gathered_results)

    # Verify timing: the last task should start at (collection_size - 1) * interval
    # Total execution time should be at least this delay
    expected_min_time = (collection_size - 1) * interval
    assert elapsed >= expected_min_time, (
        f"Execution time {elapsed:.2f}s should be at least {expected_min_time:.2f}s "
        f"for {collection_size} tasks with {interval}s interval"
    )


@pytest.mark.anyio
@pytest.mark.integration
async def test_scatter_without_interval(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
):
    """Test that scatter without interval (None) behaves normally without delays."""
    test_name = f"{test_scatter_without_interval.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Scatter without interval",
        description="Test scatter without interval has no artificial delays",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                args=ScatterArgs(
                    collection="${{ [1, 2, 3, 4] }}",
                    interval=None,  # Explicitly no interval
                ).model_dump(),
            ),
            ActionStatement(
                ref="add_one",
                action="core.transform.reshape",
                depends_on=["scatter"],
                args={"value": "${{ FN.add(ACTIONS.scatter.result, 1) }}"},
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["add_one"],
                args=GatherArgs(items="${{ ACTIONS.add_one.result }}").model_dump(),
            ),
        ],
    )

    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    start_time = time.time()

    async with test_worker_factory(temporal_client):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    end_time = time.time()
    elapsed = end_time - start_time

    # Verify results
    assert result["ACTIONS"]["gather"]["result"] == [2, 3, 4, 5]

    # Without interval, execution should be relatively fast (< 2 seconds for simple ops)
    assert elapsed < 2.0, (
        f"Execution without interval should be fast, but took {elapsed:.2f}s"
    )


@pytest.mark.anyio
@pytest.mark.integration
async def test_scatter_with_zero_interval(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
):
    """Test that scatter with interval=0 has no delays."""
    test_name = f"{test_scatter_with_zero_interval.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Scatter with zero interval",
        description="Test scatter with interval=0 has no delays",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                args=ScatterArgs(
                    collection="${{ [1, 2, 3] }}",
                    interval=0,
                ).model_dump(),
            ),
            ActionStatement(
                ref="double",
                action="core.transform.reshape",
                depends_on=["scatter"],
                args={"value": "${{ FN.mul(ACTIONS.scatter.result, 2) }}"},
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["double"],
                args=GatherArgs(items="${{ ACTIONS.double.result }}").model_dump(),
            ),
        ],
    )

    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    start_time = time.time()

    async with test_worker_factory(temporal_client):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    end_time = time.time()
    elapsed = end_time - start_time

    # Verify results
    assert result["ACTIONS"]["gather"]["result"] == [2, 4, 6]

    # With zero interval, should execute quickly
    assert elapsed < 2.0


@pytest.mark.anyio
@pytest.mark.integration
async def test_scatter_interval_with_downstream_tasks(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
):
    """Test that interval delays propagate to downstream tasks in scatter region."""
    test_name = f"{test_scatter_interval_with_downstream_tasks.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    interval = 0.3

    dsl = DSLInput(
        title="Scatter interval with downstream tasks",
        description="Test interval delays propagate through scatter region",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                args=ScatterArgs(
                    collection="${{ [10, 20, 30] }}",
                    interval=interval,
                ).model_dump(),
            ),
            # Multiple downstream tasks in the scatter region
            ActionStatement(
                ref="step1",
                action="core.transform.reshape",
                depends_on=["scatter"],
                args={"value": "${{ FN.add(ACTIONS.scatter.result, 1) }}"},
            ),
            ActionStatement(
                ref="step2",
                action="core.transform.reshape",
                depends_on=["step1"],
                args={"value": "${{ FN.add(ACTIONS.step1.result, 1) }}"},
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["step2"],
                args=GatherArgs(items="${{ ACTIONS.step2.result }}").model_dump(),
            ),
        ],
    )

    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    start_time = time.time()

    async with test_worker_factory(temporal_client):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    end_time = time.time()
    elapsed = end_time - start_time

    # Verify results: 10+1+1=12, 20+1+1=22, 30+1+1=32
    assert result["ACTIONS"]["gather"]["result"] == [12, 22, 32]

    # Verify timing includes the staggered delays
    expected_min_time = 2 * interval  # 3 items: delays at index 0, 1, 2
    assert elapsed >= expected_min_time


@pytest.mark.anyio
@pytest.mark.integration
async def test_nested_scatter_with_intervals(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
):
    """Test nested scatter operations with intervals.

    This test uses a simpler pattern matching the existing nested scatter tests.
    Structure: scatter -> scatter2 -> process -> gather -> gather2
    """
    test_name = f"{test_nested_scatter_with_intervals.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    outer_interval = 0.3
    inner_interval = 0.2

    dsl = DSLInput(
        title="Nested scatter with intervals",
        description="Test nested scatter operations both with intervals",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                args=ScatterArgs(
                    collection="${{ [[1, 2], [3, 4]] }}",
                    interval=outer_interval,
                ).model_dump(),
            ),
            ActionStatement(
                ref="scatter2",
                action="core.transform.scatter",
                depends_on=["scatter"],
                args=ScatterArgs(
                    collection="${{ ACTIONS.scatter.result }}",
                    interval=inner_interval,
                ).model_dump(),
            ),
            ActionStatement(
                ref="process",
                action="core.transform.reshape",
                depends_on=["scatter2"],
                args={"value": "${{ FN.mul(ACTIONS.scatter2.result, 10) }}"},
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["process"],
                args=GatherArgs(items="${{ ACTIONS.process.result }}").model_dump(),
            ),
            ActionStatement(
                ref="gather2",
                action="core.transform.gather",
                depends_on=["gather"],
                args=GatherArgs(items="${{ ACTIONS.gather.result }}").model_dump(),
            ),
        ],
    )

    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    start_time = time.time()

    async with test_worker_factory(temporal_client):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    end_time = time.time()
    elapsed = end_time - start_time

    # Verify results: [[10, 20], [30, 40]]
    # Only the final gather (gather2) appears in ACTIONS
    assert result["ACTIONS"]["gather2"]["result"] == [[10, 20], [30, 40]]

    # Verify timing includes both outer and inner delays
    # Outer: 2 items with outer_interval = 0.3s delay for 2nd item
    # Inner: each outer item scatters 2 items with inner_interval = 0.2s delay for 2nd item
    expected_min_time = outer_interval + inner_interval
    assert elapsed >= expected_min_time


@pytest.mark.anyio
@pytest.mark.integration
async def test_scatter_interval_with_empty_collection(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
):
    """Test that scatter with interval and empty collection doesn't hang."""
    test_name = f"{test_scatter_interval_with_empty_collection.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    dsl = DSLInput(
        title="Scatter with interval and empty collection",
        description="Test scatter with interval handles empty collection correctly",
        entrypoint=DSLEntrypoint(ref="scatter"),
        actions=[
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                args=ScatterArgs(
                    collection=[],  # Plain empty list, not expression
                    interval=1.0,  # Large interval shouldn't matter
                ).model_dump(),
            ),
            ActionStatement(
                ref="process",
                action="core.transform.reshape",
                depends_on=["scatter"],
                args={"value": "${{ ACTIONS.scatter.result }}"},
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["process"],
                args=GatherArgs(items="${{ ACTIONS.process.result }}").model_dump(),
            ),
        ],
    )

    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    start_time = time.time()

    async with test_worker_factory(temporal_client):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    end_time = time.time()
    elapsed = end_time - start_time

    # Empty collection should result in empty gather
    assert result["ACTIONS"]["gather"]["result"] == []

    # Should complete quickly despite large interval
    assert elapsed < 2.0


@pytest.mark.anyio
@pytest.mark.integration
async def test_scatter_interval_rate_limiting_use_case(
    test_role: Role,
    temporal_client: Client,
    test_worker_factory: Callable[[Client], Worker],
):
    """Test realistic rate-limiting use case with scatter interval.

    Simulates making API calls with rate limiting where we need to
    space out requests to avoid hitting rate limits.
    """
    test_name = f"{test_scatter_interval_rate_limiting_use_case.__name__}"
    wf_exec_id = generate_test_exec_id(test_name)

    # Simulate 5 API calls with 0.4s between each (2.5 calls/second)
    num_calls = 5
    interval = 0.4

    dsl = DSLInput(
        title="Rate-limited API calls with scatter",
        description="Simulate rate-limited API calls using scatter interval",
        entrypoint=DSLEntrypoint(ref="scatter_requests"),
        actions=[
            ActionStatement(
                ref="scatter_requests",
                action="core.transform.scatter",
                args=ScatterArgs(
                    collection="${{ [1, 2, 3, 4, 5] }}",
                    interval=interval,
                ).model_dump(),
            ),
            ActionStatement(
                ref="make_request",
                action="core.transform.reshape",
                depends_on=["scatter_requests"],
                args={
                    "value": {
                        "request_id": "${{ ACTIONS.scatter_requests.result }}",
                        "status": "success",
                    }
                },
            ),
            ActionStatement(
                ref="gather_responses",
                action="core.transform.gather",
                depends_on=["make_request"],
                args=GatherArgs(
                    items="${{ ACTIONS.make_request.result }}"
                ).model_dump(),
            ),
        ],
    )

    run_args = DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID)
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    start_time = time.time()

    async with test_worker_factory(temporal_client):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    end_time = time.time()
    elapsed = end_time - start_time

    # Verify all requests completed
    responses = result["ACTIONS"]["gather_responses"]["result"]
    assert len(responses) == num_calls
    assert all(r["status"] == "success" for r in responses)
    assert [r["request_id"] for r in responses] == [1, 2, 3, 4, 5]

    # Verify rate limiting timing
    expected_min_time = (num_calls - 1) * interval  # 4 * 0.4 = 1.6s
    assert elapsed >= expected_min_time, (
        f"Rate limiting failed: {num_calls} requests with {interval}s interval "
        f"should take at least {expected_min_time:.2f}s, but took {elapsed:.2f}s"
    )
