"""Integration tests for workflows.

Setup
-----
- Temporal worker runs on a different machine from

Objectives
----------
1. Test that the workflows can be executed across the Tracecat and Temporal clusters
    - We spin up a Temporal cluster for the integration tests
    - We run a worker inside of the Tracecat stack
2. Test that the workflows can be executed with the correct inputs and outputs


"""

import uuid
from pathlib import Path

import pytest
from temporalio.common import RetryPolicy

from tracecat.dsl.dispatcher import dispatch_workflow
from tracecat.dsl.workflow import DSLInput

DATA_PATH = Path(__file__).parent.parent.joinpath("data/workflows")
SHARED_TEST_DEFNS = list(DATA_PATH.glob("shared_*.yml"))
ORDERING_TEST_DEFNS = list(DATA_PATH.glob("unit_ordering_*.yml"))
IO_TEST_DEFNS = list(DATA_PATH.glob("integration_io_*.yml"))


def gen_id(name: str) -> str:
    return f"{name}-{uuid.uuid4()!s}"


# Fixture to load workflow DSLs from YAML files
@pytest.fixture
def dsl(request: pytest.FixtureRequest) -> DSLInput:
    path: list[Path] = request.param
    dsl = DSLInput.from_yaml(path)
    return dsl


@pytest.mark.parametrize("dsl", SHARED_TEST_DEFNS, indirect=True)
@pytest.mark.asyncio
async def test_workflow_can_be_dispatched(
    dsl, temporal_cluster, tracecat_worker, auth_sandbox
):
    result = await dispatch_workflow(dsl, retry_policy=RetryPolicy(maximum_attempts=1))
    assert len(result.final_context["ACTIONS"]) == len(dsl.actions)
