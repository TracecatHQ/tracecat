import os

import pytest

pytestmark = [
    pytest.mark.temporal,
    pytest.mark.usefixtures("registry_version_with_manifest"),
]

from temporalio.client import WorkflowFailureError

from tests import shared
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import RETRY_POLICIES, DSLInput, DSLRunArgs
from tracecat.dsl.workflow import DSLWorkflow


@pytest.mark.anyio
async def test_execution_fails_fatal(
    test_role, test_worker_factory, test_executor_worker_factory
):
    dsl = DSLInput.from_yaml("tests/data/workflows/unit_error_fatal.yml")
    test_name = f"test_fatal_execution-{dsl.title}"
    wf_exec_id = shared.generate_test_exec_id(test_name)
    client = await get_temporal_client()
    role = ctx_role.get()
    assert role is not None
    async with test_worker_factory(client), test_executor_worker_factory(client):
        with pytest.raises(WorkflowFailureError) as e:
            await client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(dsl=dsl, role=role, wf_id=shared.TEST_WF_ID),
                id=wf_exec_id,
                task_queue=os.environ["TEMPORAL__CLUSTER_QUEUE"],
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            )
            assert "Couldn't resolve expression 'ACTIONS.a.result.invalid'" in str(e)
