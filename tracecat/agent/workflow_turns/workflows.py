"""Run a workflow agent action's turn on an installed agent backend."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from tracecat.agent.error_policy import agent_backend_turn_failed
    from tracecat.agent.schemas import AgentOutput
    from tracecat.agent.session.activities import (
        LoadSessionMessagesInput,
        load_session_messages_activity,
    )
    from tracecat.agent.workflow_turns.activities import (
        prepare_workflow_agent_turn_activity,
        read_workflow_agent_turn_activity,
    )
    from tracecat.agent.workflow_turns.schemas import (
        ReadWorkflowAgentTurnInput,
        WorkflowAgentTurnInput,
    )
    from tracecat.dsl.common import RETRY_POLICIES
    from tracecat.temporal.errors import raise_application_error_from_classification


@workflow.defn
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
            input,
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
            # No workspace search attributes, like case-comment turns: the
            # turn is not a workflow run, and run listings must never match it,
            # even after its backend is uninstalled.
        )
        # The session's history, as the built-in workflow returns it: run
        # views render the finished transcript from it. The built-in workflow
        # loads it while its run still holds the session; here the backend's
        # turn has released it, so load it before anything else, leaving
        # another run the least time to write to the session first. A failed
        # load leaves the history out.
        message_history = None
        try:
            loaded = await workflow.execute_activity(
                load_session_messages_activity,
                LoadSessionMessagesInput(
                    role=input.args.role, session_id=turn.session_id
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
            message_history = loaded.messages
        except ActivityError as e:
            workflow.logger.warning("Failed to load workflow agent turn history: %s", e)
        read = await workflow.execute_activity(
            read_workflow_agent_turn_activity,
            ReadWorkflowAgentTurnInput(backend_id=turn.backend_id, result=result),
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RETRY_POLICIES["activity:fail_slow"],
        )
        if read.error is not None:
            raise_application_error_from_classification(
                agent_backend_turn_failed(read.error)
            )
        return AgentOutput(
            output=read.output,
            message_history=message_history,
            duration=(workflow.now() - started_at).total_seconds(),
            session_id=turn.session_id,
        )
