"""Stress tests for workflows.

These tests verify workflow execution at scale with concurrent executions
and IO-bound workloads.

Run with: uv run pytest tests/stress -x -v -s
"""

import asyncio
import os
import statistics
from dataclasses import dataclass

import pytest
from temporalio.common import (
    RetryPolicy,
    WorkflowIDReusePolicy,
)

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tests.stress.server import StressServer
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput, DSLRunArgs
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.storage.object import InlineObject


@dataclass
class BenchmarkResult:
    """Performance metrics for a stress test run."""

    name: str
    num_workflows: int
    total_time_s: float
    throughput_per_sec: float
    avg_latency_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float

    def __str__(self) -> str:
        return f"""
================================================================================
BENCHMARK: {self.name}
================================================================================
Workflows:        {self.num_workflows}
Total time:       {self.total_time_s:.2f}s
Throughput:       {self.throughput_per_sec:.2f} workflows/sec

Latency (per workflow):
  Average:        {self.avg_latency_ms:.1f}ms
  P50:            {self.p50_ms:.1f}ms
  P95:            {self.p95_ms:.1f}ms
  P99:            {self.p99_ms:.1f}ms
  Min:            {self.min_ms:.1f}ms
  Max:            {self.max_ms:.1f}ms
================================================================================
"""


def compute_benchmark_result(
    name: str, latencies_ms: list[float], total_time_s: float
) -> BenchmarkResult:
    """Compute benchmark statistics from latency measurements."""
    sorted_latencies = sorted(latencies_ms)
    n = len(sorted_latencies)
    return BenchmarkResult(
        name=name,
        num_workflows=n,
        total_time_s=total_time_s,
        throughput_per_sec=n / total_time_s if total_time_s > 0 else 0,
        avg_latency_ms=statistics.mean(sorted_latencies),
        p50_ms=sorted_latencies[int(n * 0.50)],
        p95_ms=sorted_latencies[int(n * 0.95)],
        p99_ms=sorted_latencies[int(n * 0.99)] if n >= 100 else sorted_latencies[-1],
        min_ms=sorted_latencies[0],
        max_ms=sorted_latencies[-1],
    )


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


@pytest.mark.skip(reason="This test is too slow to run on CI, and breaking atm.")
@pytest.mark.parametrize("runs", [10, 100])
@pytest.mark.slow
@pytest.mark.anyio
async def test_stress_workflow_correctness(
    runs, test_role, temporal_client, test_worker_factory
):
    """Test that we can have multiple executions of the same workflow running at the same time."""
    test_name = test_stress_workflow_correctness.__name__
    dsl = DSLInput(
        **{
            "entrypoint": {"expects": {}, "ref": "a"},
            "actions": [
                {
                    "ref": "a",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ TRIGGER.num }}",
                    },
                    "depends_on": [],
                },
                {
                    "ref": "b",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ACTIONS.a.result * 2 }}",
                    },
                    "depends_on": ["a"],
                },
                {
                    "ref": "c",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ACTIONS.b.result * 2 }}",
                    },
                    "depends_on": ["b"],
                },
                {
                    "ref": "d",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": "${{ ACTIONS.c.result * 2 }}",
                    },
                    "depends_on": ["c"],
                },
            ],
            "description": "Stress testing",
            "returns": "${{ ACTIONS.d.result }}",
            "tests": [],
            "title": f"{test_name}",
            "triggers": [],
            # When the environment is set in the config, it should override the default
            "config": {"environment": "__TEST_ENVIRONMENT__"},
        }
    )
    role = ctx_role.get()
    assert role is not None

    async with test_worker_factory(temporal_client):
        async with GatheringTaskGroup() as tg:
            # We can have multiple executions of the same workflow running at the same time
            for i in range(runs):
                wf_exec_id = generate_test_exec_id(test_name + f"-{i}")
                run_args = DSLRunArgs(
                    dsl=dsl,
                    role=role,
                    wf_id=TEST_WF_ID,
                    trigger_inputs=InlineObject(data={"num": i}),
                )
                tg.create_task(
                    temporal_client.execute_workflow(
                        DSLWorkflow.run,
                        run_args,
                        id=wf_exec_id,
                        task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
                )

    results = tg.results()
    assert len(results) == runs
    assert list(results) == [i * (2**3) for i in range(runs)]
