"""Built-in Claude agent backend, preserving the durable workflow contract."""

import copy
from typing import ClassVar

from temporalio.common import Priority
from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow

from tracecat import config
from tracecat.agent.backends.base import AgentBackend
from tracecat.agent.backends.schemas import (
    AgentWorkflowArgs,
)
from tracecat.agent.backends.types import (
    SessionForkContext,
    SessionWorkflowContext,
    TurnResult,
)
from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.schemas import AgentOutput, RunAgentArgs
from tracecat.agent.types import clamp_agent_timeout_seconds
from tracecat.dsl.common import RETRY_POLICIES


class DefaultBackend(AgentBackend[AgentWorkflowArgs, AgentOutput]):
    """Dispatch and control the existing Claude durable workflow."""

    workflow = DurableAgentWorkflow
    task_queue = config.TRACECAT__AGENT_QUEUE
    priority: ClassVar[Priority] = Priority(priority_key=1)
    retry_policy = RETRY_POLICIES["workflow:fail_fast"]
    name = "Open source"
    default_harness = "claude_code"
    supported_harnesses = frozenset({"claude_code"})
    history = None
    caller_owned_turns = True

    async def prepare_fork(self, context: SessionForkContext) -> None:
        """Copy the working snapshot; native history uses the parent session link."""
        context.fork.work_dir_snapshot = copy.deepcopy(context.parent.work_dir_snapshot)

    def parse_turn_result(self, result: object) -> TurnResult:
        """Read the output; a failed turn raises in the workflow instead."""
        return TurnResult(output=AgentOutput.model_validate(result).output)

    async def build_workflow_args(
        self, context: SessionWorkflowContext
    ) -> AgentWorkflowArgs:
        args = RunAgentArgs(
            user_prompt=context.prompt,
            session_id=context.session_id,
            active_stream_id=context.stream_id,
            curr_run_id=context.run_id,
            config=context.config,
            # Chat turns always get the agent default, never the ceiling.
            timeout_seconds=clamp_agent_timeout_seconds(None),
        )
        return AgentWorkflowArgs(
            role=context.role,
            harness_type=HarnessType(context.harness_type or self.default_harness),
            agent_args=args,
            title=context.title,
            entity_type=context.entity_type,
            entity_id=context.entity_id,
            tools=list(context.tools) if context.tools is not None else None,
            agent_preset_id=context.agent_preset_id,
            agent_preset_version_id=context.agent_preset_version_id,
        )
