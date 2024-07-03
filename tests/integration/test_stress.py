import asyncio
import os
from pathlib import Path

import pytest
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.dsl.workflow import DSLActivities, DSLRunArgs, DSLWorkflow

from ..unit.test_workflows import generate_test_exec_id

DATA_PATH = Path(__file__).parent.parent.joinpath("data/workflows")
TEST_WF_ID = "wf-00000000000000000000000000000000"


@pytest.mark.parametrize(
    "dsl",
    [DATA_PATH / "stress_adder_tree.yml"],
    ids=lambda path: path.stem,
    indirect=True,
)
@pytest.mark.parametrize(
    "num_workflows", [10, 100, 1000], ids=lambda x: f"num_workflows={x}"
)
@pytest.mark.slow
def test_concurrent_workflows(
    dsl, num_workflows, temporal_cluster, mock_registry, auth_sandbox, benchmark
):
    """Multiple executions of the same workflow run at the same time."""
    test_name = f"test_stress_workflow-{dsl.title}"

    async def run_worklows():
        tasks: list[asyncio.Task] = []
        client = await get_temporal_client()
        async with (
            Worker(
                client,
                task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                activities=DSLActivities.load(),
                workflows=[DSLWorkflow],
                workflow_runner=new_sandbox_runner(),
            ),
        ):
            async with asyncio.TaskGroup() as tg:
                # We can have multiple executions of the same workflow running at the same time
                for i in range(num_workflows):
                    wf_exec_id = generate_test_exec_id(test_name + f"-{i}")
                    task = tg.create_task(
                        client.execute_workflow(
                            DSLWorkflow.run,
                            DSLRunArgs(dsl=dsl, role=ctx_role.get(), wf_id=TEST_WF_ID),
                            id=wf_exec_id,
                            task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                            retry_policy=RetryPolicy(maximum_attempts=1),
                        )
                    )
                    tasks.append(task)
        return tasks

    tasks = benchmark.pedantic(
        lambda: asyncio.run(run_worklows()), iterations=3, rounds=1
    )
    assert all(task.done() for task in tasks)
