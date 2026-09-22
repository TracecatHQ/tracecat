"""Built-in Claude agent backend, preserving the durable workflow contract."""

from typing import ClassVar

from temporalio.common import Priority, WorkflowIDReusePolicy
from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow

from tracecat import config
from tracecat.agent.backends.base import AgentBackend
from tracecat.agent.backends.schemas import (
    AgentWorkflowArgs,
)
from tracecat.agent.backends.types import (
    AgentBackendCapability,
    SessionTurnContext,
)
from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.schemas import AgentOutput, RunAgentArgs
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.dsl.common import RETRY_POLICIES


class DefaultBackend(AgentBackend[AgentWorkflowArgs, AgentOutput]):
    """Dispatch and control the existing Claude durable workflow."""

    workflow = DurableAgentWorkflow
    task_queue = config.TRACECAT__AGENT_QUEUE
    priority: ClassVar[Priority] = Priority(priority_key=1)
    retry_policy = RETRY_POLICIES["workflow:fail_fast"]
    id_reuse_policy = WorkflowIDReusePolicy.ALLOW_DUPLICATE
    name = "Open source"
    default_harness = "claude_code"
    supported_harnesses = frozenset({"claude_code"})
    capabilities = frozenset(
        {AgentBackendCapability.FORK, AgentBackendCapability.CALLER_OWNED_WORKFLOWS}
    )
    history = None

    async def build_workflow_args(
        self, context: SessionTurnContext
    ) -> AgentWorkflowArgs:
        session = context.session
        args = RunAgentArgs(
            user_prompt=context.prompt,
            session_id=session.id,
            active_stream_id=context.stream_id,
            curr_run_id=context.run_id,
            config=context.config,
        )
        return AgentWorkflowArgs(
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
