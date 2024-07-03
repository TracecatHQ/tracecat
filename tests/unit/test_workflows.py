"""Unit tests for workflows.

Objectives
----------
1. Test that the workflows can be executed in an isolated environment
2. Test that the workflows can be executed with the correct inputs and outputs
3. Test that the workflows can be executed with the correct ordering

"""

import asyncio
import os
from pathlib import Path

import pytest
from loguru import logger
from slugify import slugify
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.dsl.workflow import DSLActivities, DSLContext, DSLRunArgs, DSLWorkflow
from tracecat.expressions.shared import ExprContext
from tracecat.identifiers.resource import ResourcePrefix
from tracecat.types.exceptions import TracecatExpressionError

DATA_PATH = Path(__file__).parent.parent.joinpath("data/workflows")
SHARED_TEST_DEFNS = [path.stem for path in DATA_PATH.glob("shared_*.yml")]
ORDERING_TEST_DEFNS = [path.stem for path in DATA_PATH.glob("unit_ordering_*.yml")]


TEST_WF_ID = "wf-00000000000000000000000000000000"


def generate_test_exec_id(name: str) -> str:
    return (
        TEST_WF_ID
        + f":{ResourcePrefix.WORKFLOW_EXECUTION}-"
        + slugify(name, separator="_")
    )


@pytest.mark.parametrize("dsl", SHARED_TEST_DEFNS, indirect=True)
@pytest.mark.asyncio
async def test_workflow_can_run_from_yaml(
    dsl, temporal_cluster, mock_registry, auth_sandbox
):
    test_name = f"test_workflow_can_run_from_yaml-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)
    client = await get_temporal_client()
    # Run workflow
    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=DSLActivities.load(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=ctx_role.get(), wf_id=TEST_WF_ID),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

    logger.info(result)
    assert len(result[ExprContext.ACTIONS]) == len(dsl.actions)


@pytest.mark.asyncio
async def test_workflow_udf_registry_function_can_be_called(mock_registry):
    """We need to test that the ordering of the workflow tasks is correct."""
    udf = mock_registry.get("integration_test.count")
    for i in range(10):
        assert i == udf.fn()


@pytest.mark.asyncio
async def test_workflow_udf_registry_async_function_can_be_called(mock_registry):
    """We need to test that the ordering of the workflow tasks is correct."""
    udf = mock_registry.get("integration_test.passthrough")

    async def coro(i: int):
        v = await udf.fn(num=i)
        assert i == v

    async with asyncio.TaskGroup() as tg:
        tasks = []
        for i in range(10):
            tasks.append(tg.create_task(coro(i)))


def assert_respectful_exec_order(dsl: DSLInput, final_context: DSLContext):
    act_outputs = final_context[ExprContext.ACTIONS]
    for action in dsl.actions:
        target = action.ref
        for source in action.depends_on:
            source_order = act_outputs[source]["result"]
            target_order = act_outputs[target]["result"]
            assert source_order < target_order


@pytest.mark.parametrize("dsl", ORDERING_TEST_DEFNS, indirect=True)
@pytest.mark.asyncio
async def test_workflow_ordering_is_correct(
    dsl, temporal_cluster, mock_registry, auth_sandbox
):
    """We need to test that the ordering of the workflow tasks is correct."""

    # Connect client

    test_name = f"test_workflow_ordering_is_correct-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)
    client = await get_temporal_client()
    # Run a worker for the activities and workflow
    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=DSLActivities.load(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=ctx_role.get(), wf_id=TEST_WF_ID),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
    # Iterate over the actual ordering of the tasks
    # and compare that in the topological ordering every LHS task in a pair executed before the RHS task

    # Check that the execution order respects the graph edges
    assert_respectful_exec_order(dsl, result)


@pytest.mark.parametrize(
    "dsl_with_expected",
    [
        "unit_conditional_adder_tree_skips",
        "unit_conditional_adder_tree_continues",
        "unit_conditional_adder_tree_skip_propagates",
        "unit_conditional_adder_diamond_skip_with_join_weak_dep",
        "unit_transform_forwarder_loop",
        "unit_transform_forwarder_loop_chained",
        "unit_transform_forwarder_arrange",
        "unit_transform_forwarder_arrange_loop",
        "unit_transform_forwarder_zip",
        "unit_transform_forwarder_map_loop",
        "unit_runtime_test_adder_tree",
        "unit_runtime_test_chain",
    ],
    indirect=True,
    ids=lambda x: x,
)
@pytest.mark.asyncio
async def test_workflow_completes_and_correct(
    dsl_with_expected, temporal_cluster, mock_registry, auth_sandbox
):
    dsl, expected = dsl_with_expected
    test_name = f"test_correctness_execution-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)

    client = await get_temporal_client()
    # Run a worker for the activities and workflow
    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=DSLActivities.load(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=ctx_role.get(), wf_id=TEST_WF_ID),
            id=wf_exec_id,
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
    assert result == expected


@pytest.mark.parametrize(
    "dsl",
    ["unit_conditional_adder_diamond_skip_with_join_strong_dep_fails"],
    indirect=True,
)
@pytest.mark.asyncio
@pytest.mark.skip
async def test_conditional_execution_fails(
    dsl, temporal_cluster, mock_registry, auth_sandbox
):
    test_name = f"test_conditional_execution-{dsl.title}"
    wf_exec_id = generate_test_exec_id(test_name)
    client = await get_temporal_client()
    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=DSLActivities.load(),
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        # NOTE: I can't seem to figure out how to catch the exception thrown by the workflow
        # We need to figure out how to bubble up certain exceptions to the client
        # Or allow certain exceptions to control workflow execution
        with pytest.raises(TracecatExpressionError) as e:
            await client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(dsl=dsl, role=ctx_role.get(), wf_id=TEST_WF_ID),
                id=wf_exec_id,
                task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                retry_policy=RetryPolicy(
                    maximum_attempts=0,
                    non_retryable_error_types=[
                        "tracecat.types.exceptions.TracecatExpressionError"
                        "TracecatValidationError"
                    ],
                ),
            )
        assert "Operand has no path" in str(e)
