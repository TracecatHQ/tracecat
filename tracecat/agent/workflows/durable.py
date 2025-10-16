from __future__ import annotations

import uuid
from collections.abc import AsyncIterable
from datetime import timedelta
from typing import Any, Literal, cast

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.durable_exec.temporal import TemporalRunContext
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    ModelRequest,
)
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
)
from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from tracecat.agent.activities import (
        AgentActivities,
        BuildToolDefsArgs,
        EventStreamHandlerArgs,
    )
    from tracecat.agent.approvals.service import ApprovalManager, ApprovalMap
    from tracecat.agent.context import AgentContext
    from tracecat.agent.durable import DurableModel
    from tracecat.agent.models import ModelInfo, RunAgentArgs, ToolFilters
    from tracecat.agent.stream.common import PersistableStreamingAgentDepsSpec
    from tracecat.agent.toolset import RemoteToolset
    from tracecat.contexts import ctx_role
    from tracecat.dsl.common import RETRY_POLICIES
    from tracecat.logger import logger
    from tracecat.types.auth import Role


class AgentWorkflowArgs(BaseModel):
    role: Role
    agent_args: RunAgentArgs


class WorkflowApprovalSubmission(BaseModel):
    approvals: ApprovalMap
    approved_by: uuid.UUID | None = None


@workflow.defn
class DurableAgentWorkflow:
    @workflow.init
    def __init__(self, args: AgentWorkflowArgs):
        self.role = args.role
        ctx_role.set(args.role)
        AgentContext.set(session_id=args.agent_args.session_id)

        self._status: Literal["running", "waiting_for_results", "done"] = "running"
        # Probably want to keep a dict of this instead of overwritting it
        self._deferred_tool_results: DeferredToolResults = DeferredToolResults()
        self._turn: int = 0
        if args.role.workspace_id is None:
            raise ApplicationError("Role must have a workspace ID", non_retryable=True)
        self.workspace_id = args.role.workspace_id
        self.session_id = args.agent_args.session_id
        self.run_ctx_type = TemporalRunContext[PersistableStreamingAgentDepsSpec]
        self.approvals = ApprovalManager(role=self.role)

    @workflow.run
    async def run(
        self, args: AgentWorkflowArgs
    ) -> AgentRunResult[Any | DeferredToolRequests]:
        """Run the agent until completion. The agent will call tools until it needs human approval."""
        logger.info("DurableAgentWorkflow run", args=args)
        ext_toolset = await self._build_toolset(args)
        logger.info("TOOLSET", toolset=ext_toolset)
        logger.info("AGENT CONTEXT", agent_context=AgentContext.get())
        if workflow.unsafe.is_replaying():
            logger.info("Workflow is replaying")
        else:
            logger.info("Starting agent", prompt=args.agent_args.user_prompt)

        messages: list[ModelMessage] = [
            ModelRequest.user_text_prompt(args.agent_args.user_prompt)
        ]
        model = DurableModel(
            role=args.role,
            info=ModelInfo(
                name=args.agent_args.config.model_name,
                provider=args.agent_args.config.model_provider,
                base_url=args.agent_args.config.base_url,
            ),
            activity_config=workflow.ActivityConfig(
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            ),
            deps_type=PersistableStreamingAgentDepsSpec,
        )
        logger.info("DURABLE MODEL", model_info=model._info)

        agent = Agent(
            model,
            name="durable-agent",
            output_type=[str, DeferredToolRequests],
            instructions="You are a helpful assistant.",
            deps_type=PersistableStreamingAgentDepsSpec,
            event_stream_handler=self._event_stream_handler,
            toolsets=[ext_toolset],
        )

        deps = PersistableStreamingAgentDepsSpec(
            session_id=self.session_id,
            workspace_id=self.workspace_id,
            persistent=False,
            namespace="agent",
        )
        # TODO: Implement max turns
        while True:
            logger.info("Running agent", turn=self._turn)
            result = await agent.run(
                message_history=messages,
                deferred_tool_results=self.approvals.get(),
                deps=deps,
            )
            logger.warning("AGENT RUN RESULT", result=result)

            # perf: Can probably early exit here if the result is not a DeferredToolRequests
            messages = result.all_messages()

            match result.output:
                case DeferredToolRequests(approvals=approvals):
                    logger.info(
                        "Waiting for tool results",
                        turn=self._turn,
                        approvals=approvals,
                    )
                    # If there are approvals, we need to wait for the tool results
                    if approvals:
                        await self.approvals.prepare(approvals)
                        # Wait for the approval results
                        await self.approvals.wait()
                        logger.info(
                            "Tool results", deferred_tool_results=self.approvals.get()
                        )
                        await self.approvals.handle_decisions()
                    # The next run() of the workflow will handle the tool calls
                    # depending on the results of the approvals
                case _:
                    return cast(AgentRunResult[Any], result)
            self._turn += 1

    @workflow.update
    def set_approvals(self, submission: WorkflowApprovalSubmission) -> None:
        submission = WorkflowApprovalSubmission.model_validate(submission)
        logger.info(
            "Setting approvals",
            approvals=submission.approvals,
            approved_by=submission.approved_by,
        )
        self.approvals.set(submission.approvals, approved_by=submission.approved_by)

    @set_approvals.validator
    def validate_set_approvals(self, submission: WorkflowApprovalSubmission) -> None:
        """Ensure all expected tool approvals are provided."""
        submission = WorkflowApprovalSubmission.model_validate(submission)
        logger.info(
            "Validating approvals update",
            approvals=list(submission.approvals.keys()),
            approved_by=submission.approved_by,
        )
        self.approvals.validate_responses(submission.approvals)

    async def _build_toolset(
        self, args: AgentWorkflowArgs
    ) -> RemoteToolset[PersistableStreamingAgentDepsSpec]:
        build_tool_defs_result = await workflow.execute_activity_method(
            AgentActivities.build_tool_definitions,
            arg=BuildToolDefsArgs(
                tool_filters=ToolFilters(
                    namespaces=args.agent_args.config.namespaces,
                    actions=args.agent_args.config.actions,
                )
            ),
            start_to_close_timeout=timedelta(seconds=120),
        )
        return RemoteToolset(build_tool_defs_result.tool_definitions)

    async def _event_stream_handler(
        self,
        ctx: RunContext[PersistableStreamingAgentDepsSpec],
        events: AsyncIterable[AgentStreamEvent],
    ) -> None:
        logger.info(
            "WF Event stream handler",
            in_activity=activity.in_activity(),
            events_type=type(events),
        )
        serialized_run_context = self.run_ctx_type.serialize_run_context(ctx)
        async for event in events:
            await workflow.execute_activity_method(
                AgentActivities.event_stream_handler,
                args=(
                    EventStreamHandlerArgs(
                        event=event,
                        serialized_run_context=serialized_run_context,
                    ),
                    ctx.deps,
                ),
                start_to_close_timeout=timedelta(seconds=60),
            )
