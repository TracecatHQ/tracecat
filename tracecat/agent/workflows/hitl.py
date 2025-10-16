from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterable
from typing import Literal, cast

from pydantic import BaseModel
from pydantic_ai import Agent, ApprovalRequired, RunContext, ToolDefinition
from pydantic_ai.durable_exec.temporal import TemporalAgent
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    ModelRequest,
)
from pydantic_ai.run import AgentRunResult
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from tracecat.agent.models import RunAgentArgs, StreamingAgentDeps
    from tracecat.agent.stream.common import (
        PersistableStreamingAgentDepsSpec,
    )
    from tracecat.agent.stream.writers import (
        event_stream_handler as _event_stream_handler,
    )
    from tracecat.agent.workflows.proxy import LazyModel, ModelInfo
    from tracecat.logger import logger
    from tracecat.types.auth import Role


async def event_stream_handler(
    ctx: RunContext[PersistableStreamingAgentDepsSpec],
    events: AsyncIterable[AgentStreamEvent],
) -> None:
    # Concrete deps
    logger.info("Building deps")
    deps = await ctx.deps.build()
    # RunContext with concrete deps
    # Create a new RunContext with concrete deps by using dataclasses.replace
    new_ctx = cast(RunContext[StreamingAgentDeps], dataclasses.replace(ctx, deps=deps))
    await _event_stream_handler(new_ctx, events)


async def add_descriptions(
    ctx: RunContext[PersistableStreamingAgentDepsSpec], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition] | None:
    logger.info(
        "Adding descriptions",
        tool_defs=tool_defs,
        in_workflow=workflow.in_workflow(),
        in_activity=activity.in_activity(),
    )


_hitl_agent = Agent(
    LazyModel(
        ModelInfo(name="gpt-4o-mini", provider="openai", base_url="asdlkfjhasld")
    ),
    name="hitl_agent",
    output_type=[str, DeferredToolRequests],
    event_stream_handler=event_stream_handler,
    instructions="You are a helpful assistant.",
    deps_type=PersistableStreamingAgentDepsSpec,
    prepare_tools=add_descriptions,
)


@_hitl_agent.tool
async def get_file(
    ctx: RunContext[PersistableStreamingAgentDepsSpec], tool_name: str
) -> None:
    logger.info("Invoking tool", tool_name=tool_name)
    raise CallDeferred


@_hitl_agent.tool
async def delete_file(
    ctx: RunContext[PersistableStreamingAgentDepsSpec], tool_name: str, tool_args: str
) -> None:
    logger.info("Invoking tool", tool_name=tool_name)
    raise CallDeferred


hitl_temporal_agent = TemporalAgent(_hitl_agent, name="hitl_temporal_agent")


class HitlAgentWorkflowArgs(BaseModel):
    role: Role
    agent_args: RunAgentArgs


@workflow.defn
class HitlAgentWorkflow:
    @workflow.init
    def __init__(self, args: HitlAgentWorkflowArgs):
        self._status: Literal["running", "waiting_for_results", "done"] = "running"
        # Probably want to keep a dict of this instead of overwritting it
        self._deferred_tool_requests: DeferredToolRequests | None = None
        self._deferred_tool_results: DeferredToolResults | None = None
        self._turn: int = 0
        if args.role.workspace_id is None:
            raise ApplicationError("Role must have a workspace ID", non_retryable=True)
        self.workspace_id = args.role.workspace_id
        self.session_id = args.agent_args.session_id

    @workflow.run
    async def run(
        self, args: HitlAgentWorkflowArgs
    ) -> AgentRunResult[str | DeferredToolRequests]:
        """Run the agent until completion. The agent will call tools until it needs human approval."""
        if workflow.unsafe.is_replaying():
            logger.info("Workflow is replaying")
        else:
            logger.info("Starting agent", prompt=args.agent_args.user_prompt)

        messages: list[ModelMessage] = [
            ModelRequest.user_text_prompt(args.agent_args.user_prompt)
        ]
        model = LazyModel(
            model_info=ModelInfo(
                name=args.agent_args.config.model_name,
                provider=args.agent_args.config.model_provider,
                base_url=args.agent_args.config.base_url,
            )
        )
        logger.info("LAZYMODEL", model_info=model._model_info)
        agent = Agent(
            model,
            name=_hitl_agent.name,
            output_type=[str, DeferredToolRequests],
            event_stream_handler=event_stream_handler,
            instructions="You are a helpful assistant.",
            deps_type=PersistableStreamingAgentDepsSpec,
            prepare_tools=add_descriptions,
        )

        @agent.tool
        def create_file(
            ctx: RunContext[PersistableStreamingAgentDepsSpec], path: str
        ) -> None:
            raise CallDeferred

        @agent.tool
        def delete_file(
            ctx: RunContext[PersistableStreamingAgentDepsSpec], path: str
        ) -> bool:
            if not ctx.tool_call_approved:
                raise ApprovalRequired
            return True

        temporal_agent = TemporalAgent(agent, name=hitl_temporal_agent.name)

        deps = PersistableStreamingAgentDepsSpec(
            session_id=self.session_id,
            workspace_id=self.workspace_id,
            persistent=False,
            namespace="agent",
        )
        while True:
            logger.info("Running agent", turn=self._turn)
            result = await temporal_agent.run(
                message_history=messages,
                deferred_tool_results=self._deferred_tool_results,
                deps=deps,
            )

            # perf: Can probably early exit here if the result is not a DeferredToolRequests
            messages = result.all_messages()

            if isinstance(result.output, DeferredToolRequests):
                self._deferred_tool_requests = result.output
                self._deferred_tool_results = None
                self._status = "waiting_for_results"

                logger.info("Waiting for tool results", turn=self._turn)
                #
                await workflow.wait_condition(
                    lambda: self._deferred_tool_results is not None
                )
                # The next run() of the workflow will handle the tool calls
                # depending on the results of the approvals
            else:
                self._status = "done"
                return result
            self._turn += 1

    @workflow.query
    def get_status(self) -> Literal["running", "waiting_for_results", "done"]:
        """Get the current status of the workflow."""
        return self._status

    @workflow.query
    def get_deferred_tool_requests(self) -> DeferredToolRequests | None:
        """Ideally, we write pending approvals to the DB instead of holding in workflow state."""
        return self._deferred_tool_requests

    @workflow.query
    def get_workspace_id(self) -> str:
        """Get the workspace ID associated with this workflow."""
        return str(self.workspace_id)

    @workflow.query
    def get_session_id(self) -> str:
        """Get the session ID associated with this workflow."""
        return str(self.session_id)

    @workflow.signal
    def set_deferred_tool_results(self, results: DeferredToolResults) -> None:
        """Receive tool approvals from outside the workflow."""
        self._status = "running"
        self._deferred_tool_requests = None
        self._deferred_tool_results = results
