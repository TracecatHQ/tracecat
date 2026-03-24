"""Public agent execution service (CE)."""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any, Final

from pydantic_ai import UsageLimits
from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.run import AgentRunResult
from pydantic_ai.tools import DeferredToolRequests

from tracecat.agent.common.stream_types import (
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.executor.base import BaseAgentExecutor, BaseAgentRunHandle
from tracecat.agent.factory import AgentFactory, build_agent
from tracecat.agent.fallback import (
    FallbackAttemptFailure,
    classify_fallback_failure,
    format_fallback_failure_message,
    format_model_target,
    get_fallback_configs,
    should_retry_same_turn,
)
from tracecat.agent.runtime.pydantic_ai.adapter import PydanticAIAdapter
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.stream.events import StreamError
from tracecat.agent.stream.writers import event_stream_handler
from tracecat.agent.types import StreamingAgentDeps
from tracecat.auth.types import Role
from tracecat.chat.constants import APPROVAL_REQUEST_HEADER
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


def _is_user_visible_stream_event(event: UnifiedStreamEvent) -> bool:
    return event.type not in {
        StreamEventType.DONE,
        StreamEventType.ERROR,
        StreamEventType.USER_MESSAGE,
    }


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

        # Message history loading removed - pydantic-ai persistence path deprecated
        message_history: list[ModelMessage] | None = None

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
            usage = UsageLimits(
                request_limit=args.max_requests or 50,
                tool_calls_limit=args.max_tool_calls,
            )
            user_prompt_value = None if args.is_continuation else args.user_prompt
            adapter = PydanticAIAdapter()
            failures: list[FallbackAttemptFailure] = []
            candidate_configs = get_fallback_configs(args.config)

            for index, (target, candidate_config) in enumerate(candidate_configs):
                emitted_visible_output = False

                async def tracking_event_stream_handler(
                    ctx: Any, events: AsyncIterable[AgentStreamEvent]
                ) -> None:
                    async def tracked_events() -> AsyncIterator[AgentStreamEvent]:
                        nonlocal emitted_visible_output
                        async for event in events:
                            unified_event = adapter.to_unified_event(event)
                            if _is_user_visible_stream_event(unified_event):
                                emitted_visible_output = True
                            yield event

                    await self._event_stream_handler(ctx, tracked_events())

                try:
                    logger.info(
                        "Starting streaming agent attempt",
                        session_id=args.session_id,
                        candidate=index + 1,
                        candidate_model=format_model_target(target),
                    )
                    agent = await self._factory(candidate_config)
                    result = await agent.run(
                        user_prompt=user_prompt_value,
                        message_history=message_history,
                        deferred_tool_results=args.deferred_tool_results,
                        deps=self.deps,
                        event_stream_handler=tracking_event_stream_handler,
                        usage_limits=usage,
                    )
                    new_messages = result.new_messages()

                    match result.output:
                        # Immediately stream the approval request message to the client
                        case DeferredToolRequests(approvals=approvals) if approvals:
                            # Build the ModelResponse for persistence (unchanged format)
                            approval_message = ModelResponse(
                                parts=[
                                    TextPart(content=APPROVAL_REQUEST_HEADER),
                                    *approvals,
                                ]
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
                                    approval_event = (
                                        UnifiedStreamEvent.approval_request_event(
                                            approval_items
                                        )
                                    )
                                    await self.deps.stream_writer.stream.append(
                                        approval_event
                                    )
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
                    break
                except Exception as exc:
                    error_message = str(exc)
                    failures.append(
                        FallbackAttemptFailure(
                            target=target,
                            error=error_message,
                        )
                    )
                    last_candidate = index == len(candidate_configs) - 1
                    failure_scope = classify_fallback_failure(error_message)
                    retry_same_turn = (
                        should_retry_same_turn(error_message)
                        and not emitted_visible_output
                        and not last_candidate
                    )
                    if retry_same_turn:
                        logger.warning(
                            "Streaming agent attempt failed before visible output; retrying the same turn with a fallback candidate",
                            session_id=args.session_id,
                            attempt=index + 1,
                            total_attempts=len(candidate_configs),
                            candidate_model=format_model_target(target),
                            error=error_message,
                            failure_scope=failure_scope,
                            retry_same_turn=True,
                        )
                        continue
                    raise RuntimeError(
                        format_fallback_failure_message(failures)
                    ) from exc
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

        return result
