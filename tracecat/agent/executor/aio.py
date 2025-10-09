"""Public agent execution service (CE)."""

import asyncio
from typing import Any, Final

from pydantic_ai import Agent
from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    UserPromptPart,
)
from pydantic_ai.run import AgentRunResult

from tracecat.agent.executor.base import BaseAgentExecutor, BaseAgentRunHandle
from tracecat.agent.models import RunAgentArgs, ToolFilters
from tracecat.agent.providers import get_model
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
from tracecat.types.auth import Role


class AioAgentRunHandle[T](BaseAgentRunHandle[T]):
    """Handle for CE runs executed as asyncio tasks."""

    def __init__(self, task: asyncio.Task[T], run_id: str):
        super().__init__(run_id)
        self._task: Final[asyncio.Task[T]] = task

    async def result(self) -> T:
        res = await self._task
        if res is None:
            raise RuntimeError("Streaming agent run did not complete successfully.")
        return res

    async def cancel(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            return


class AioStreamingAgentExecutor(BaseAgentExecutor):
    """Execute an agent directly in an asyncio task."""

    def __init__(
        self,
        role: Role | None = None,
        writer_cls: type[PersistentStreamWriter] = PersistentStreamWriter,
        event_stream_handler: EventStreamHandler[
            BasicStreamingAgentDeps
        ] = event_stream_handler,
        **kwargs: Any,
    ):
        super().__init__(role, **kwargs)
        self._writer_cls = writer_cls
        self._event_stream_handler = event_stream_handler

    async def _get_writer(self, args: RunAgentArgs) -> PersistentStreamWriter:
        """Get the appropriate stream writer for the agent."""
        client = await get_redis_client()
        session_id = args.session_id
        return self._writer_cls(
            stream=AgentStream(client, session_id), session_id=session_id
        )

    async def start(
        self, args: RunAgentArgs
    ) -> BaseAgentRunHandle[AgentRunResult[str] | None]:
        """Start an agentic run with streaming."""
        coro = self._start_agent(args)
        task: asyncio.Task[AgentRunResult[str] | None] = asyncio.create_task(coro)
        return AioAgentRunHandle(task, run_id=str(args.session_id))

    async def _start_agent(self, args: RunAgentArgs) -> AgentRunResult[str] | None:
        # Fire-and-forget execution using the agent function directly
        logger.info("Starting streaming agent")

        async with get_async_session_context_manager() as session:
            agent_svc = AgentManagementService(session, self.role)
            chat_svc = ChatService(session, self.role)
            session_id = args.session_id

            try:
                message_history = await chat_svc.list_messages(session_id)
            except Exception as e:
                logger.warning(
                    "Failed to load message history from database, starting fresh",
                    error=str(e),
                    session_id=session_id,
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
                    event_stream_handler=self._event_stream_handler,
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
                        chat_id=session_id,
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
