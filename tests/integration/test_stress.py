import asyncio
import uuid
from pathlib import Path

import pytest

from tracecat.dsl.dispatcher import dispatch_workflow

DATA_PATH = Path(__file__).parent.parent.joinpath("data/workflows")
TEST_WF_ID = "wf-00000000000000000000000000000000"


@pytest.mark.parametrize(
    "dsl",
    ["stress_adder_tree"],
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

    def generate_wf_id():
        return f"wf-{uuid.uuid4()}"

    async def run_workflows():
        tasks = [
            dispatch_workflow(dsl=dsl, wf_id=generate_wf_id())
            for _ in range(num_workflows)
        ]
        return await asyncio.gather(*tasks)

    benchmark.pedantic(lambda: asyncio.run(run_workflows()), iterations=3, rounds=1)
