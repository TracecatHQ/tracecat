"""Stress tests for workflows.

These tests verify workflow execution at scale with concurrent executions
and IO-bound workloads.

Run with: uv run pytest tests/stress -x -v -s
"""

import asyncio
import os

import pytest
from temporalio.common import (
    RetryPolicy,
    WorkflowIDReusePolicy,
)

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tests.stress.server import StressServer
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLRunArgs
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.storage.object import InlineObject


@pytest.mark.parametrize("dsl", ["stress_adder_tree"], indirect=True, ids=lambda x: x)
@pytest.mark.slow
@pytest.mark.anyio
async def test_stress_workflow(dsl, test_role, test_worker_factory):
    """Test that we can have multiple executions of the same workflow running at the same time."""
    test_name = f"test_stress_workflow-{dsl.title}"
    client = await get_temporal_client()
    role = ctx_role.get()
    assert role is not None

    tasks: list[asyncio.Task] = []
    async with test_worker_factory(client):
        async with asyncio.TaskGroup() as tg:
            # We can have multiple executions of the same workflow running at the same time
            for i in range(1000):
                wf_exec_id = generate_test_exec_id(test_name + f"-{i}")
                task = tg.create_task(
                    client.execute_workflow(
                        DSLWorkflow.run,
                        DSLRunArgs(dsl=dsl, role=role, wf_id=TEST_WF_ID),
                        id=wf_exec_id,
                        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                        retry_policy=RetryPolicy(maximum_attempts=1),
                        id_reuse_policy=WorkflowIDReusePolicy.TERMINATE_IF_RUNNING,
                    )
                )
                tasks.append(task)

    assert all(task.done() for task in tasks)


@pytest.mark.parametrize(
    "dsl", ["stress_adder_tree_io"], indirect=True, ids=lambda x: x
)
@pytest.mark.slow
@pytest.mark.anyio
async def test_stress_workflow_io(dsl, test_role, test_worker_factory):
    """Test IO-bound workflow stress with multiple concurrent executions.

    This test:
    1. Starts a local HTTP server that simulates IO-bound work (configurable delay)
    2. Runs 100 concurrent workflow executions
    3. Each workflow makes 7 HTTP requests (tree structure: A -> B,C -> D,E,F,G)
    4. Validates all executions complete successfully
    """
    test_name = f"test_stress_workflow_io-{dsl.title}"
    client = await get_temporal_client()
    num_workflows = 100
    delay_ms = 50  # Base delay per HTTP call
    jitter_ms = 25  # Random jitter
    role = ctx_role.get()
    assert role is not None

    tasks: list[asyncio.Task] = []

    # Start the stress server in a subprocess
    # Use docker_url since executor runs inside Docker container
    async with StressServer() as server, test_worker_factory(client):
        async with asyncio.TaskGroup() as tg:
            # Run multiple concurrent workflow executions
            for i in range(num_workflows):
                wf_exec_id = generate_test_exec_id(test_name + f"-{i}")
                task = tg.create_task(
                    client.execute_workflow(
                        DSLWorkflow.run,
                        DSLRunArgs(
                            dsl=dsl,
                            role=role,
                            wf_id=TEST_WF_ID,
                            trigger_inputs=InlineObject(
                                data={
                                    "stress_server_url": server.docker_url,
                                    "delay_ms": delay_ms,
                                    "jitter_ms": jitter_ms,
                                },
                            ),
                        ),
                        id=wf_exec_id,
                        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                        retry_policy=RetryPolicy(maximum_attempts=1),
                        id_reuse_policy=WorkflowIDReusePolicy.TERMINATE_IF_RUNNING,
                    )
                )
                tasks.append(task)

    assert all(task.done() for task in tasks)
