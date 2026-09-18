"""Built-in Claude session backend, preserving the durable workflow contract."""

from uuid import UUID

from temporalio.client import Client
from temporalio.common import Priority

from tracecat import config
from tracecat.agent.cancellation import signal_turn_cancel
from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.backends.schemas import (
    AgentWorkflowArgs,
    WorkflowCancelRequest,
)
from tracecat.agent.session.backends.types import (
    SessionDispatchUncertain,
    SessionTurnContext,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.logger import logger


class DurableSessionBackend:
    """Dispatch and control the existing Claude durable workflow."""

    name = "Standard"
    default_harness = "claude_code"
    supported_harnesses = frozenset({"claude_code"})
    supports_fork = True
    supports_caller_owned_workflows = True
    approval_update_name = "set_approvals"
    history = None

    def is_enabled(self) -> bool:
        return True

    def workflow_id(self, run_id: UUID) -> str:
        return f"agent/{run_id}"

    async def start_turn(self, context: SessionTurnContext) -> None:
        session = context.session
        args = RunAgentArgs(
            user_prompt=context.prompt,
            session_id=session.id,
            active_stream_id=context.stream_id,
            curr_run_id=context.run_id,
            config=context.config,
        )
        workflow_args = AgentWorkflowArgs(
            role=context.role,
            harness_type=HarnessType(session.harness_type or self.default_harness),
            agent_args=args,
            title=session.title,
            entity_type=AgentSessionEntity(session.entity_type),
            entity_id=session.entity_id,
            tools=session.tools,
            agent_preset_id=session.agent_preset_id,
            agent_preset_version_id=session.agent_preset_version_id,
        )
        client = await get_temporal_client()
        session.curr_run_id = context.run_id
        session.active_stream_id = context.stream_id
        session.last_error = None
        context.db.add(session)
        await context.db.commit()
        try:
            await client.start_workflow(
                "DurableAgentWorkflow",
                workflow_args,
                id=self.workflow_id(context.run_id),
                task_queue=config.TRACECAT__AGENT_QUEUE,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                priority=Priority(priority_key=1),
                search_attributes=context.search_attributes,
            )
        except Exception:
            pass
        else:
            return
        # Raise after leaving the handler so SDK context cannot leak.
        raise SessionDispatchUncertain("Dispatch requires reconciliation")

    async def cancel(self, client: Client, run_id: UUID) -> None:
        # The executor polls this signal for prompt cancellation. The workflow
        # update remains authoritative if the best-effort signal is unavailable.
        try:
            await signal_turn_cancel(str(run_id), reason="user_cancel")
        except Exception:
            logger.warning("Failed to write turn cancel signal", run_id=str(run_id))
        await client.get_workflow_handle(self.workflow_id(run_id)).execute_update(
            "request_cancel", WorkflowCancelRequest(reason="user_cancel")
        )
