"""Public agent execution service (CE)."""

import asyncio
import uuid
from typing import Any, Final, cast

import orjson
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    UserPromptPart,
)
from pydantic_ai.run import AgentRunResult

from tracecat.agent.executor.base import BaseAgentExecutor, BaseAgentRunHandle
from tracecat.agent.models import RunAgentArgs, RunAgentResult, ToolFilters
from tracecat.agent.providers import get_model
from tracecat.agent.runtime import (
    AgentOutput,
    run_agent,
)
from tracecat.agent.service import AgentManagementService
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamError
from tracecat.agent.stream.writers import (
    BasicStreamingAgentDeps,
    PersistentStreamWriter,
    event_stream_handler,
)
from tracecat.agent.tools import build_agent_tools
from tracecat.chat.service import ChatService
from tracecat.db.engine import get_async_session_context_manager
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.settings.service import get_setting_cached


class AioAgentRunHandle[T](BaseAgentRunHandle):
    """Handle for CE runs executed as asyncio tasks."""

    def __init__(self, task: asyncio.Task[T], run_id: str):
        super().__init__(run_id)
        self._task: Final[asyncio.Task[T]] = task

    async def result(self) -> RunAgentResult:
        res = await self._task
        if res is None:
            raise RuntimeError("Streaming agent run did not complete successfully.")
        output = AgentOutput.model_validate(res)
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
        task: asyncio.Task[dict[str, Any]] = asyncio.create_task(self._run_agent(args))
        return AioAgentRunHandle(task, run_id=args.session_id)

    async def _run_agent(self, args: RunAgentArgs) -> dict[str, Any]:
        async with get_async_session_context_manager() as session:
            agent_svc = AgentManagementService(session, self.role)
            fixed_args_str = cast(
                str, await get_setting_cached("agent_fixed_args", session=session)
            )
            try:
                fixed_args = (
                    cast(dict[str, Any], orjson.loads(fixed_args_str))
                    if fixed_args_str
                    else {}
                )
            except Exception:
                logger.warning(
                    "Failed to parse fixed args", fixed_args_str=fixed_args_str
                )
                fixed_args = {}
            logger.info("Fixed args", fixed_args=fixed_args)

            async with agent_svc.with_model_config() as model_config:
                tool_filters = args.tool_filters or ToolFilters.default()
                return await run_agent(
                    instructions=args.instructions,
                    user_prompt=args.user_prompt,
                    fixed_arguments=fixed_args,
                    model_name=model_config.name,
                    model_provider=model_config.provider,
                    actions=tool_filters.actions or [],
                    stream_id=args.session_id,
                )


class AioStreamingAgentExecutor(BaseAgentExecutor):
    """Execute a workflow builder agent turn directly in an asyncio task."""

    async def _get_writer(self, args: RunAgentArgs) -> PersistentStreamWriter:
        """Get the appropriate stream writer for the agent."""
        client = await get_redis_client()
        session_id = uuid.UUID(args.session_id)
        return PersistentStreamWriter(
            stream=AgentStream(client, session_id), chat_id=session_id
        )

    async def start(self, args: RunAgentArgs) -> BaseAgentRunHandle:
        """Start an agentic run with streaming."""
        coro = self._start_agent(args)
        task: asyncio.Task[AgentRunResult[str] | None] = asyncio.create_task(coro)
        return AioAgentRunHandle(task, run_id=args.session_id)

    async def _start_agent(self, args: RunAgentArgs) -> AgentRunResult[str] | None:
        # Fire-and-forget execution using the agent function directly
        logger.info("Starting streaming agent")

        async with get_async_session_context_manager() as session:
            agent_svc = AgentManagementService(session, self.role)
            chat_svc = ChatService(session, self.role)
            chat_id = uuid.UUID(args.session_id)

            try:
                message_history = await chat_svc.list_messages(chat_id)
            except Exception as e:
                logger.warning(
                    "Failed to load message history from database, starting fresh",
                    error=str(e),
                    session_id=args.session_id,
                )
                message_history = []

            logger.info(
                "Loaded message history",
                message_count=len(message_history),
            )

            # 2. Prepare writer
            writer = await self._get_writer(args)
            # Immediately stream the user's prompt to the client
            user_message = ModelRequest(
                parts=[UserPromptPart(content=args.user_prompt)]
            )
            await writer.stream.append(user_message)

            deps = BasicStreamingAgentDeps(stream_writer=writer)

            result: AgentRunResult[str] | None = None
            new_messages: list[ModelRequest | ModelResponse] | None = None

            async with agent_svc.with_model_config() as model_config:
                tools = await build_agent_tools(
                    actions=(args.tool_filters or ToolFilters.default()).actions or [],
                )
                agent = Agent(
                    model=get_model(model_config.name, model_config.provider),
                    instructions=args.instructions,
                    output_type=str,
                    event_stream_handler=event_stream_handler,
                    deps_type=BasicStreamingAgentDeps,
                    tools=tools.tools,
                )
                try:
                    result = await agent.run(
                        args.user_prompt,
                        output_type=str,
                        deps=deps,
                        message_history=message_history,
                    )
                    new_messages = result.new_messages()
                except Exception as exc:
                    error_message = str(exc)
                    logger.error(
                        "Streaming agent run failed",
                        error=str(exc),
                        chat_id=args.session_id,
                    )
                    await writer.stream.error(error_message)
                    ## Don't update the message history with the error message
                    new_messages = [
                        user_message,
                        StreamError.model_response(error_message),
                    ]
                finally:
                    # Ensure we always close the stream so the client stops waiting.
                    await writer.stream.done()

        if new_messages:
            await writer.store(new_messages)

        return result
