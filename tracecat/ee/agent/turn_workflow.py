from __future__ import annotations as _annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from pydantic_core import to_json

    from tracecat import config
    from tracecat.ee.agent.activities import execute_tool_call, model_request
    from tracecat.ee.agent.core import run_agent_loop
    from tracecat.ee.agent.models import (
        AgentDeps,
        AgentTurnWorkflowArgs,
        ExecuteToolCallArgs,
        ExecuteToolCallResult,
        ModelRequestArgs,
        ModelRequestResult,
        ToolFilters,
    )


@workflow.defn
class AgentTurnWorkflow:
    @workflow.init
    def __init__(self, args: AgentTurnWorkflowArgs) -> None:
        # Safety parameters (following Gemini CLI pattern)
        self.max_turns_per_message: int = 25
        self.current_turn_count: int = 0
        self.deps = AgentDeps(
            call_model=self._call_model,
            call_tool=self._call_tool,
        )

    async def _call_model(self, args: ModelRequestArgs) -> ModelRequestResult:
        return await workflow.execute_activity(
            model_request,
            args,
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
        )

    async def _call_tool(self, args: ExecuteToolCallArgs) -> ExecuteToolCallResult:
        return await workflow.execute_activity(
            execute_tool_call,
            args,
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
        )

    @workflow.run
    async def run(self, args: AgentTurnWorkflowArgs) -> str:
        # Initialize message history if provided
        # We should load this from the database/redis

        # Handle the single message with agentic loop
        # await self._handle_user_message(message)
        message_history = []
        result = await run_agent_loop(
            user_prompt=args.user_prompt,
            messages=message_history,
            tool_filters=args.tool_filters or ToolFilters.default(),
            deps=self.deps,
            max_turns=self.max_turns_per_message,
        )

        # Return the updated message history
        return to_json(result.message_history).decode()
