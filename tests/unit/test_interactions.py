import asyncio
import os
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from temporalio.client import Client

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat.contexts import ctx_interaction
from tracecat.dsl.common import RETRY_POLICIES, DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.models import ActionStatement
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.ee.interactions.enums import InteractionStatus, InteractionType
from tracecat.ee.interactions.models import (
    InteractionContext,
    InteractionInput,
    ResponseInteraction,
)
from tracecat.ee.interactions.service import InteractionService
from tracecat.expressions.functions import get_interaction
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.logger import logger
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


@contextmanager
def _set_interaction_context(interaction: InteractionContext | None):
    """Helper to set and reset interaction context."""
    token = ctx_interaction.set(interaction)
    try:
        yield
    finally:
        ctx_interaction.reset(token)


def test_interaction_context() -> None:
    """Test get_interaction function with and without context."""
    # Test with no interaction context set
    assert get_interaction() is None
    wf_id = WorkflowUUID.new_uuid4()
    wf_exec_id = generate_exec_id(wf_id)

    # Test with interaction context set
    interaction1 = InteractionContext(
        interaction_id=uuid.uuid4(),
        execution_id=wf_exec_id,
        action_ref="test-action",
    )
    interaction2 = InteractionContext(
        interaction_id=uuid.uuid4(),
        execution_id=wf_exec_id,
        action_ref="test-action",
    )

    with _set_interaction_context(interaction1):
        assert get_interaction() == interaction1.model_dump()
        with _set_interaction_context(interaction2):
            assert get_interaction() == interaction2.model_dump()
        assert get_interaction() == interaction1.model_dump()

    # Test with no interaction context set
    assert get_interaction() is None


@pytest.mark.anyio
@pytest.mark.integration
async def test_workflow_interaction(
    svc_role: Role, temporal_client: Client, test_worker_factory
):
    role = svc_role
    test_name = test_workflow_interaction.__name__

    # Create table with a number column
    # Create workflow that inserts rows in a loop and then looks them up
    dsl = DSLInput(
        title=test_name,
        description="Test running table actions in a loop",
        entrypoint=DSLEntrypoint(ref="process_numbers"),
        actions=[
            ActionStatement(
                ref="a",
                action="core.transform.reshape",
                interaction=ResponseInteraction(type=InteractionType.RESPONSE),
                args={"value": "${{ FN.get_interaction() }}"},
            ),
        ],
        returns={
            "result": "${{ ACTIONS.a.result }}",
            "fn_interaction": "${{ FN.get_interaction() }}",
            "interaction": "${{ ACTIONS.a.interaction }}",
            "interaction_id": "${{ ACTIONS.a.interaction_id }}",
        },
    )

    # Run the workflow
    wf_exec_id = generate_test_exec_id(test_name) + str(uuid.uuid4())
    run_args = DSLRunArgs(dsl=dsl, role=role, wf_id=TEST_WF_ID)
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]

    try:
        async with test_worker_factory(temporal_client, task_queue=queue):
            wf_handle = await temporal_client.start_workflow(
                DSLWorkflow.run,
                run_args,
                id=wf_exec_id,
                task_queue=queue,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                execution_timeout=timedelta(seconds=10),
            )
            async with InteractionService.with_session(role=role) as svc:
                # Handling the interaction state
                while True:
                    await asyncio.sleep(0.1)
                    # Let's query the interaction state
                    if interactions := await svc.list_interactions(
                        wf_exec_id=wf_exec_id
                    ):
                        # Loop until we get a pending interaction
                        assert len(interactions) == 1
                        interaction = interactions[0]
                        # NOTE: We need to refresh the interaction to get the latest state
                        # Since we're still inside the transaction
                        await svc.session.refresh(interaction)
                        interaction_id = interaction.id
                        assert interaction.action_ref == "a"
                        assert interaction.response_payload is None
                        if interaction.status == InteractionStatus.PENDING:
                            # Pending -> we have started waiting for a response
                            break
                        assert interaction.status == InteractionStatus.IDLE

            # Now, manually update the workflow to add an interaction
            input = InteractionInput(
                interaction_id=interaction_id,
                execution_id=wf_exec_id,
                action_ref="a",
                data={"incoming": "test"},
            )
            result = await wf_handle.execute_update(
                DSLWorkflow.interaction_handler, input
            )
            assert result.message == "success"
            assert result.detail == {"incoming": "test"}

            exec_result = await wf_handle.result()
            logger.info(exec_result)
            assert exec_result["result"] == input.model_dump(
                exclude={"data"}, mode="json"
            )
            assert exec_result["fn_interaction"] is None
            assert exec_result["interaction"] == {"incoming": "test"}
            assert exec_result["interaction_id"] == str(interaction_id)
    except Exception:
        try:
            await wf_handle.terminate(reason="Test complete")
        except Exception:
            pass
        raise
