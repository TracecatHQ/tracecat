import asyncio
import uuid
from pathlib import Path

import pytest

from tracecat.dsl.common import DSLInput
from tracecat.dsl.dispatcher import dispatch_workflow

DATA_PATH = Path(__file__).parent.parent.joinpath("data/workflows")
TEST_WF_ID = "wf-00000000000000000000000000000000"


@pytest.mark.parametrize(
    "path_to_dsl",
    [DATA_PATH / "stress_adder_tree.yml"],
    ids=lambda path: path.stem,
    indirect=True,
)
@pytest.mark.parametrize(
    "num_workflows", [10, 100, 1000], ids=lambda x: f"num_workflows={x}"
)
@pytest.mark.slow
def test_concurrent_workflows(
    path_to_dsl, num_workflows, temporal_cluster, mock_registry, auth_sandbox, benchmark
):
    """Multiple executions of the same workflow run at the same time."""

    def generate_wf_id():
        return f"wf-{uuid.uuid4()}"

    tasks = [
        dispatch_workflow(
            dsl=DSLInput.from_yaml(path_to_dsl),
            wf_id=generate_wf_id(),
        )
        for _ in range(num_workflows)
    ]
    workflow_runs = benchmark.pedantic(
        lambda: asyncio.gather(tasks()), iterations=3, rounds=1
    )
    assert all(run.done() for run in workflow_runs)
