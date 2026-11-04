"""Run a workflow agent action's turn on an installed agent backend."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from tracecat.agent.schemas import AgentOutput
    from tracecat.agent.workflow_turns.activities import (
        prepare_workflow_agent_turn_activity,
        read_workflow_agent_turn_activity,
    )
    from tracecat.agent.workflow_turns.schemas import (
        WORKFLOW_AGENT_TURN_WORKFLOW,
        PrepareWorkflowAgentTurnInput,
        ReadWorkflowAgentTurnInput,
        WorkflowAgentTurnInput,
    )
    from tracecat.dsl.common import RETRY_POLICIES


@workflow.defn(name=WORKFLOW_AGENT_TURN_WORKFLOW)
class WorkflowAgentTurnWorkflow:
    """Prepare and run the backend's turn, and return it as an AgentOutput.

    The workflow agent action starts this as its child, so the action's
    history records an AgentOutput, or a failure when the turn ended without
    output, rather than the backend's own result type. Reruns reuse that
    result, and run views read it without the backend installed.

    The turn is reserved here, so a reservation is never left behind by a
    parent that could still start its turn: once this workflow has closed,
    another action may take the session over.
    """

    @workflow.run
    async def run(self, input: WorkflowAgentTurnInput) -> AgentOutput:
        info = workflow.info()
        started_at = workflow.now()
        turn = await workflow.execute_activity(
            prepare_workflow_agent_turn_activity,
            PrepareWorkflowAgentTurnInput(
                role=input.args.role,
                run_id=input.run_id,
                backend_id=input.backend_id,
                args=input.args,
            ),
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RETRY_POLICIES["activity:fail_slow"],
        )
        # Started by type name: the backend's workflow and output types are
        # never imported here.
        result = await workflow.execute_child_workflow(
            turn.workflow_type,
            turn.args,
            id=turn.workflow_id,
            task_queue=turn.task_queue,
            retry_policy=turn.retry_policy,
            # The turn's run ID was persisted before this start, so recovery
            # may have tombstoned it. Never run it then.
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            priority=turn.priority,
            execution_timeout=info.execution_timeout,
            task_timeout=info.task_timeout,
            search_attributes=info.typed_search_attributes,
        )
        read = await workflow.execute_activity(
            read_workflow_agent_turn_activity,
            ReadWorkflowAgentTurnInput(backend_id=turn.backend_id, result=result),
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RETRY_POLICIES["activity:fail_slow"],
        )
        if read.error is not None:
            raise ApplicationError(read.error, non_retryable=True)
        return AgentOutput(
            output=read.output,
            duration=(workflow.now() - started_at).total_seconds(),
            session_id=turn.session_id,
        )
