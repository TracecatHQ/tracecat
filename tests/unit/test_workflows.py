"""Unit tests for workflows.

Objectives
----------
1. Test that the workflows can be executed in an isolated environment
2. Test that the workflows can be executed with the correct inputs and outputs
3. Test that the workflows can be executed with the correct ordering

"""

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from loguru import logger
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from tracecat.contexts import ctx_role
from tracecat.dsl.common import get_temporal_client
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.dsl.workflow import (
    DSLContext,
    DSLInput,
    DSLRunArgs,
    DSLWorkflow,
    dsl_activities,
)

DATA_PATH = Path(__file__).parent.parent.joinpath("data/workflows")
SHARED_TEST_DEFNS = list(DATA_PATH.glob("shared_*.yml"))
ORDERING_TEST_DEFNS = list(DATA_PATH.glob("unit_ordering_*.yml"))


def gen_id(name: str) -> str:
    return f"{name}-{uuid.uuid4()!s}"


@pytest.fixture
def mock_registry():
    """Mock registry for testing UDFs.

    Note
    ----
    - This fixture is used to test the integration of UDFs with the workflow.
    - It's unreachable by an external worker, as the worker will not have access
    to these functions when it starts up.
    """
    from tracecat.registry import registry

    # NOTE!!!!!!!: Didn't want to spend too much time figuring out how
    # to grab the actual execution order using the client, so I'm using a
    # hacky way to get the order of execution. TO FIX LATER
    # The counter doesn't get reset properly so you should never use this outside
    # of the 'ordering' tests
    def counter():
        i = 0
        while True:
            yield i
            i += 1

    counter_gen = counter()
    if "integration_test.count" not in registry:

        @registry.register(
            description="Counts up from 0",
            namespace="integration_test",
        )
        def count(arg: str | None = None) -> int:
            order = next(counter_gen)
            return order

    if "integration_test.passthrough" not in registry:

        @registry.register(
            description="passes through",
            namespace="integration_test",
        )
        async def passthrough(num: int) -> int:
            await asyncio.sleep(0.1)
            return num

    registry.init()
    yield registry
    counter_gen = counter()  # Reset the counter generator


# Fixture to load workflow DSLs from YAML files
@pytest.fixture
def dsl(request: pytest.FixtureRequest) -> DSLInput:
    path: list[Path] = request.param
    dsl = DSLInput.from_yaml(path)
    return dsl


# Fixture to load yaml files from name
@pytest.fixture
def expected(request: pytest.FixtureRequest) -> dict[str, Any]:
    path: Path = request.param
    with path.open() as f:
        yaml_data = f.read()
    return yaml.safe_load(yaml_data)


@pytest.mark.parametrize("dsl", SHARED_TEST_DEFNS, indirect=True)
@pytest.mark.asyncio
async def test_workflow_can_run_from_yaml(
    dsl, temporal_cluster, mock_registry, auth_sandbox
):
    client = await get_temporal_client()
    # Run workflow
    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=dsl_activities,
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=ctx_role.get()),
            id=gen_id(f"test_workflow_can_run_from_yaml-{dsl.title}"),
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

    logger.info(result)
    assert len(result["ACTIONS"]) == len(dsl.actions)


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
    act_outputs = final_context["ACTIONS"]
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

    client = await get_temporal_client()
    # Run a worker for the activities and workflow
    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=dsl_activities,
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=ctx_role.get()),
            id=gen_id(f"test_workflow_ordering_is_correct-{dsl.title}"),
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
    # Iterate over the actual ordering of the tasks
    # and compare that in the topological ordering every LHS task in a pair executed before the RHS task

    # Check that the execution order respects the graph edges
    assert_respectful_exec_order(dsl, result)


@pytest.mark.parametrize(
    "dsl,expected",
    [
        (
            DATA_PATH / "unit_conditional_adder_tree_halt.yml",
            DATA_PATH / "unit_conditional_adder_tree_halt_expected.yaml",
        ),
        (
            DATA_PATH / "unit_conditional_adder_tree_continues.yml",
            DATA_PATH / "unit_conditional_adder_tree_continues_expected.yaml",
        ),
        # (
        #     DATA_PATH / "unit_conditional_adder_tree_halt_with_propagation.yml",
        #     DATA_PATH / "unit_conditional_adder_tree_halt_with_propagation.yaml",
        # ),
    ],
    indirect=True,
)
@pytest.mark.asyncio
async def test_conditional_execution(
    dsl, expected, temporal_cluster, mock_registry, auth_sandbox
):
    """We need to test that the ordering of the workflow tasks is correct."""

    # Connect client

    client = await get_temporal_client()
    # Run a worker for the activities and workflow
    async with Worker(
        client,
        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
        activities=dsl_activities,
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=ctx_role.get()),
            id=gen_id(f"test_conditional_execution-{dsl.title}"),
            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
    assert result == expected
