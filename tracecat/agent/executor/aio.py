"""Public agent execution service (CE)."""

import asyncio
from typing import Any, Final, cast

import orjson
from tracecat_registry.integrations.agents.builder import AgentOutput, run_agent

from tracecat.agent.executor.base import BaseAgentExecutor, BaseAgentRunHandle
from tracecat.agent.models import RunAgentArgs, RunAgentResult, ToolFilters
from tracecat.agent.service import AgentManagementService
from tracecat.logger import logger
from tracecat.settings.service import get_setting_cached


class AioAgentRunHandle[T](BaseAgentRunHandle):
    """Handle for CE runs executed as asyncio tasks."""

    def __init__(self, task: asyncio.Task[T], run_id: str):
        super().__init__(run_id)
        self._task: Final[asyncio.Task[T]] = task

    async def result(self) -> RunAgentResult:
        raw = await self._task
        output = AgentOutput.model_validate(raw)
        return RunAgentResult(messages=output.message_history)

    async def cancel(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            return


class AioAgentExecutor(BaseAgentExecutor):
    """Execute an agent turn directly in an asyncio task."""

    async def start(self, args: RunAgentArgs) -> BaseAgentRunHandle:
        # Fire-and-forget execution using the agent function directly
        agent_svc = AgentManagementService(self.session, self.role)
        fixed_args_str = cast(
            str, await get_setting_cached("agent_fixed_args", session=self.session)
        )
        try:
            fixed_args = cast(dict[str, Any], orjson.loads(fixed_args_str))
        except Exception:
            logger.warning("Failed to parse fixed args", fixed_args_str=fixed_args_str)
            fixed_args = {}
        logger.info("Fixed args", fixed_args=fixed_args)
        async with agent_svc.with_model_config() as model_config:
            tool_filters = args.tool_filters or ToolFilters.default()
            coro = run_agent(
                instructions=args.instructions,
                user_prompt=args.user_prompt,
                fixed_arguments=fixed_args,
                model_name=model_config.name,
                model_provider=model_config.provider,
                actions=tool_filters.actions or [],
                stream_id=args.session_id,
            )
            task: asyncio.Task[dict[str, Any]] = asyncio.create_task(coro)
        return AioAgentRunHandle(task, run_id=args.session_id)
