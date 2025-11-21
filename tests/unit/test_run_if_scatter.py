import pytest

from tests.shared import generate_test_exec_id
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs, RETRY_POLICIES
from tracecat.dsl.schemas import ActionStatement, GatherArgs, ScatterArgs
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.identifiers.workflow import WorkflowUUID


@pytest.mark.anyio
async def test_run_if_in_scatter_can_read_parent_actions(
    test_role, temporal_client, test_worker_factory
) -> None:
    dsl = DSLInput(
        title="run_if inherits parent actions in scatter",
        description="Ensure run_if can access ACTIONS from parent streams during scatter",
        entrypoint=DSLEntrypoint(ref="a"),
        actions=[
            ActionStatement(
                ref="a",
                action="core.transform.reshape",
                args={"value": "${{ 5 }}"},
            ),
            ActionStatement(
                ref="scatter",
                action="core.transform.scatter",
                depends_on=["a"],
                args=ScatterArgs(collection=[1, 2, 3]).model_dump(),
            ),
            ActionStatement(
                ref="inner",
                action="core.transform.reshape",
                depends_on=["scatter"],
                run_if="${{ ACTIONS.a.result == 5 }}",
                args={
                    "value": "${{ ACTIONS.scatter.result + ACTIONS.a.result }}",
                },
            ),
            ActionStatement(
                ref="gather",
                action="core.transform.gather",
                depends_on=["inner"],
                args=GatherArgs(items="${{ ACTIONS.inner.result }}").model_dump(),
            ),
        ],
        returns="${{ ACTIONS.gather.result }}",
    )

    run_args = DSLRunArgs(
        dsl=dsl,
        role=test_role,
        wf_id=WorkflowUUID.new("wf-00000000000000000000000000000007"),
    )
    wf_exec_id = generate_test_exec_id(test_run_if_in_scatter_can_read_parent_actions.__name__)

    worker = test_worker_factory(temporal_client)
    async with worker:
        result = await worker.client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=worker.task_queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )

    assert result == [6, 7, 8]
