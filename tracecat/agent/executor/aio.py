"""Public agent execution service (CE)."""

import asyncio
from typing import Any, Final

from pydantic_ai import UsageLimits
from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    UserPromptPart,
)
from pydantic_ai.run import AgentRunResult

from tracecat.agent.executor.base import BaseAgentExecutor, BaseAgentRunHandle
from tracecat.agent.factory import AgentFactory, build_agent
from tracecat.agent.models import RunAgentArgs, StreamingAgentDeps
from tracecat.agent.stream.events import StreamError
from tracecat.agent.stream.writers import event_stream_handler
from tracecat.logger import logger
from tracecat.types.auth import Role


class AioAgentRunHandle[T](BaseAgentRunHandle[T]):
    """Handle for CE runs executed as asyncio tasks."""

    def __init__(self, task: asyncio.Task[T], run_id: str):
        super().__init__(run_id)
        self._task: Final[asyncio.Task[T]] = task

    async def result(self) -> T:
        res = await self._task
        if res is None:
            raise RuntimeError(
                "Streaming agent run did not complete successfully. The selected "
                "model may not support streaming responses. Try switching to a "
                "model with streaming support or disable streaming."
            )
        return res

    async def cancel(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            return


# This is an execution harness for an agent that adds persistence + streaming
class AioStreamingAgentExecutor(BaseAgentExecutor[AgentRunResult[str] | None]):
    """Execute an agent directly in an asyncio task."""

    def __init__(
        self,
        deps: StreamingAgentDeps,
        role: Role | None = None,
        event_stream_handler: EventStreamHandler[
            StreamingAgentDeps
        ] = event_stream_handler,
        factory: AgentFactory = build_agent,
        **kwargs: Any,
    ):
        super().__init__(role, **kwargs)
        self.deps = deps
        self._event_stream_handler = event_stream_handler
        self._factory = factory

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

        if self.deps.message_store:
            message_history = await self.deps.message_store.load(args.session_id)
        else:
            message_history = None

        # 2. Prepare writer
        # Immediately stream the user's prompt to the client
        user_message = ModelRequest(parts=[UserPromptPart(content=args.user_prompt)])
        await self.deps.stream_writer.stream.append(user_message)

        result: AgentRunResult[str] | None = None
        new_messages: list[ModelRequest | ModelResponse] | None = None

        agent = await self._factory(args.config)
        usage = UsageLimits(
            request_limit=args.max_requests or 50,
            tool_calls_limit=args.max_tool_calls,
        )
        try:
            result = await agent.run(
                user_prompt=args.user_prompt,
                message_history=message_history,
                deps=self.deps,
                event_stream_handler=self._event_stream_handler,
                usage_limits=usage,
            )
            new_messages = result.new_messages()
        except Exception as exc:
            error_message = str(exc)
            logger.error(
                "Streaming agent run failed",
                error=error_message,
                session_id=args.session_id,
            )
            await self.deps.stream_writer.stream.error(error_message)
            ## Don't update the message history with the error message
            new_messages = [
                user_message,
                StreamError.model_response(error_message),
            ]
        finally:
            # Ensure we always close the stream so the client stops waiting.
            await self.deps.stream_writer.stream.done()

        if new_messages and self.deps.message_store:
            await self.deps.message_store.store(args.session_id, new_messages)

        return result
