"""Public agent execution service (CE)."""

import asyncio
from typing import Any, Final

from pydantic_ai import UsageLimits
from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.run import AgentRunResult
from pydantic_ai.tools import DeferredToolRequests

from tracecat.agent.executor.base import BaseAgentExecutor, BaseAgentRunHandle
from tracecat.agent.factory import AgentFactory, build_agent
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.shared.stream_types import ToolCallContent, UnifiedStreamEvent
from tracecat.agent.stream.events import StreamError
from tracecat.agent.stream.writers import event_stream_handler
from tracecat.agent.types import StreamingAgentDeps
from tracecat.auth.types import Role
from tracecat.chat.constants import APPROVAL_REQUEST_HEADER
from tracecat.chat.enums import MessageKind
from tracecat.config import TRACECAT__UNIFIED_AGENT_STREAMING_ENABLED
from tracecat.logger import logger


class AioAgentRunHandle[T](BaseAgentRunHandle[T]):
    """Handle for CE runs executed as asyncio tasks."""

    def __init__(self, task: asyncio.Task[T], run_id: str):
        super().__init__(run_id)
        self._task: Final[asyncio.Task[T]] = task

    async def result(self) -> T:
        res = await self._task
        if res is None:
            raise RuntimeError("Agent run did not complete successfully.")
        return res

    async def cancel(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            return


# This is an execution harness for an agent that adds persistence + streaming
type ExecutorResult = AgentRunResult[str | DeferredToolRequests] | None


class AioStreamingAgentExecutor(BaseAgentExecutor[ExecutorResult]):
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

    async def start(self, args: RunAgentArgs) -> BaseAgentRunHandle[ExecutorResult]:
        """Start an agentic run with streaming."""
        coro = self._start_agent(args)
        task: asyncio.Task[ExecutorResult] = asyncio.create_task(coro)
        return AioAgentRunHandle(task, run_id=str(args.session_id))

    async def _start_agent(self, args: RunAgentArgs) -> ExecutorResult:
        # CE executor requires config to be provided (no preset support)
        if args.config is None:
            raise ValueError("config is required for AioStreamingAgentExecutor")

        # Fire-and-forget execution using the agent function directly
        logger.info(
            "Starting streaming agent",
            session_id=args.session_id,
            max_requests=args.max_requests,
            max_tool_calls=args.max_tool_calls,
            is_continuation=args.is_continuation,
            model_name=args.config.model_name,
            model_provider=args.config.model_provider,
        )

        if self.deps.message_store:
            loaded_history = await self.deps.message_store.load(args.session_id)
            message_history: list[ModelMessage] | None = []
            for chat_message in loaded_history:
                # Only include pydantic-ai messages in the history
                if isinstance(chat_message.message, (ModelRequest | ModelResponse)):
                    message_history.append(chat_message.message)
        else:
            message_history = None

        # 2. Prepare writer
        # Immediately stream the user's prompt to the client unless continuation
        user_message: ModelRequest | None = None
        if not args.is_continuation:
            user_message = ModelRequest(
                parts=[UserPromptPart(content=args.user_prompt)]
            )
            if TRACECAT__UNIFIED_AGENT_STREAMING_ENABLED:
                # Unified streaming: use UnifiedStreamEvent for all events
                user_event = UnifiedStreamEvent.user_message_event(args.user_prompt)
                await self.deps.stream_writer.stream.append(user_event)
            else:
                # Legacy streaming: append raw ModelRequest
                await self.deps.stream_writer.stream.append(user_message)

        result: ExecutorResult = None
        new_messages: list[ModelMessage] | None = None
        approval_message: ModelResponse | None = None

        try:
            agent = await self._factory(args.config)
            usage = UsageLimits(
                request_limit=args.max_requests or 50,
                tool_calls_limit=args.max_tool_calls,
            )
            user_prompt_value = None if args.is_continuation else args.user_prompt
            result = await agent.run(
                user_prompt=user_prompt_value,
                message_history=message_history,
                deferred_tool_results=args.deferred_tool_results,
                deps=self.deps,
                event_stream_handler=self._event_stream_handler,
                usage_limits=usage,
            )
            new_messages = result.new_messages()

            match result.output:
                # Immediately stream the approval request message to the client
                case DeferredToolRequests(approvals=approvals) if approvals:
                    # Build the ModelResponse for persistence (unchanged format)
                    approval_message = ModelResponse(
                        parts=[TextPart(content=APPROVAL_REQUEST_HEADER), *approvals]
                    )
                    try:
                        if TRACECAT__UNIFIED_AGENT_STREAMING_ENABLED:
                            # Unified streaming: emit harness-agnostic event
                            approval_items = [
                                ToolCallContent(
                                    id=call.tool_call_id,
                                    name=call.tool_name,
                                    input=call.args_as_dict(),
                                )
                                for call in approvals
                            ]
                            approval_event = UnifiedStreamEvent.approval_request_event(
                                approval_items
                            )
                            await self.deps.stream_writer.stream.append(approval_event)
                        else:
                            # Legacy streaming: append raw ModelResponse
                            await self.deps.stream_writer.stream.append(
                                approval_message
                            )
                    except Exception as e:
                        logger.warning(
                            "Failed to stream approval request",
                            error=str(e),
                            session_id=args.session_id,
                        )
        except Exception as exc:
            error_message = str(exc)
            logger.error(
                "Streaming agent run failed",
                error=error_message,
                session_id=args.session_id,
            )
            await self.deps.stream_writer.stream.error(error_message)
            ## Don't update the message history with the error message
            new_messages = []
            if user_message is not None:
                new_messages.append(user_message)
            new_messages.append(StreamError.model_response(error_message))
        finally:
            # Ensure we always close the stream so the client stops waiting.
            await self.deps.stream_writer.stream.done()

        if store := self.deps.message_store:
            if new_messages:
                await store.store(args.session_id, new_messages)
            if approval_message:
                await store.store(
                    args.session_id,
                    [approval_message],
                    kind=MessageKind.APPROVAL_REQUEST,
                )

        return result
