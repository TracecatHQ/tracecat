import asyncio
from collections.abc import AsyncIterable
from typing import Any, Protocol

import aiohttp
import orjson
from pydantic import TypeAdapter
from pydantic_ai.messages import (
    AgentStreamEvent,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
)
from pydantic_ai.tools import RunContext
from pydantic_core import to_jsonable_python
from tracecat_registry.integrations.agents.builder import (
    create_tool_call,
    create_tool_return,
)
from tracecat_registry.integrations.agents.tokens import (
    DATA_KEY,
    END_TOKEN,
    END_TOKEN_VALUE,
)

from tracecat.logger import logger
from tracecat.redis.client import RedisClient

ta: TypeAdapter[AgentStreamEvent] = TypeAdapter(AgentStreamEvent)


class StreamWriter(Protocol):
    async def write(self, events: AsyncIterable[AgentStreamEvent]) -> None: ...


class HasStreamWriter(Protocol):
    stream_writer: StreamWriter


class HttpStreamWriter(StreamWriter):
    def __init__(self, url: str = "http://localhost:1234/stream"):
        self.url = url

    async def write(self, events: AsyncIterable[AgentStreamEvent]) -> None:
        async with aiohttp.ClientSession() as session:
            async for event in events:
                logger.warning("STREAM EVENT", event=event)
                # Make a post request to the API to stream the event
                async with session.post(
                    self.url, json={"event": ta.dump_json(event).decode()}
                ) as response:
                    logger.warning("STREAM RESPONSE", response=response.status)


class RedisStreamWriter(StreamWriter):
    def __init__(self, client: RedisClient, stream_key: str):
        self.client = client
        self.stream_key = stream_key
        self._accumulated_text = ""
        self._in_text_response = False

    async def _stream_message(self, message: Any) -> None:
        try:
            await self.client.xadd(
                self.stream_key,
                {DATA_KEY: orjson.dumps(message, default=to_jsonable_python).decode()},
                maxlen=10000,
                approximate=True,
            )
        except Exception as e:
            logger.warning("Failed to stream message", error=e)

    async def write(self, events: AsyncIterable[AgentStreamEvent]) -> None:
        try:
            async for event in events:
                logger.debug("Processing stream event", event_type=type(event).__name__)

                if isinstance(event, FunctionToolCallEvent):
                    # Tool call event - write as full ModelMessage
                    tool_call_msg = create_tool_call(
                        tool_name=event.part.tool_name,
                        tool_args=event.part.args or {},
                        tool_call_id=event.part.tool_call_id,
                    )
                    await self._stream_message(tool_call_msg)

                elif isinstance(event, FunctionToolResultEvent):
                    # Tool result event - write as full ModelMessage
                    if event.result.tool_name:  # Only process if tool_name is not None
                        tool_return_msg = create_tool_return(
                            tool_name=event.result.tool_name,
                            content=event.result.content,
                            tool_call_id=event.result.tool_call_id,
                        )
                        await self._stream_message(tool_return_msg)
                elif isinstance(event, PartStartEvent):
                    # Start of a text response - initialize state
                    if hasattr(event.part, "content") or isinstance(
                        event.part, TextPart
                    ):
                        self._in_text_response = True
                        self._accumulated_text = ""

                elif isinstance(event, PartDeltaEvent):
                    # Text delta - write immediately and accumulate
                    if (
                        hasattr(event.delta, "part_delta_kind")
                        and event.delta.part_delta_kind == "text"
                    ):
                        # Write delta entry immediately
                        delta_payload = {
                            "t": "delta",
                            "text": event.delta.content_delta,
                        }
                        await self._stream_message(delta_payload)
                        # Accumulate for final message
                        self._accumulated_text += event.delta.content_delta

                elif isinstance(event, FinalResultEvent):
                    # Final result - write complete assistant message if we had text
                    if self._in_text_response and self._accumulated_text:
                        final_msg = ModelResponse(
                            parts=[TextPart(content=self._accumulated_text)]
                        )
                        await self._stream_message(final_msg)

                        # Reset state
                        self._in_text_response = False
                        self._accumulated_text = ""

        finally:
            # Always add end marker when streaming is complete
            await self._stream_message({END_TOKEN: END_TOKEN_VALUE})


class BroadcastStreamWriter(StreamWriter):
    def __init__(self, writers: list[StreamWriter]):
        self.writers = writers
        # Create a queue for each writer
        self.queues: list[asyncio.Queue[AgentStreamEvent | None]] = [
            asyncio.Queue(maxsize=10) for _ in range(len(self.writers))
        ]

    async def _fanout_events(self, events: AsyncIterable[AgentStreamEvent]) -> None:
        """
        Reads from the input events and puts each event into all writer queues.
        Puts a sentinel (None) at the end to signal completion.
        """
        try:
            async for event in events:
                for queue in self.queues:
                    await queue.put(event)
        finally:
            # Signal completion to all queues
            for queue in self.queues:
                await queue.put(None)

    async def write(self, events: AsyncIterable[AgentStreamEvent]) -> None:
        """
        Write events to all writers, cloning the stream for each writer to ensure live streaming.

        Args:
            events: An async iterable of AgentStreamEvent objects.

        Raises:
            TypeError: If events is not an AsyncIterable.
        """
        if not hasattr(events, "__aiter__"):
            raise TypeError("events must be an AsyncIterable")

        # Buffer events as they arrive and broadcast to all writers in real time.
        # Each writer gets its own async queue, and a background task feeds events to all queues.
        # This ensures that all writers receive the same events, even if they consume at different speeds.

        async def writer_task(
            writer: StreamWriter, queue: asyncio.Queue[AgentStreamEvent | None]
        ) -> None:
            """
            Consumes events from its queue and writes them to the writer.
            """

            async def event_gen() -> AsyncIterable[AgentStreamEvent]:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield item

            await writer.write(event_gen())

        # Start the fanout task
        fanout = asyncio.create_task(self._fanout_events(events))

        # Start a task for each writer
        writer_tasks = [
            asyncio.create_task(writer_task(writer, queue))
            for writer, queue in zip(self.writers, self.queues, strict=False)
        ]

        # Wait for all tasks to complete
        try:
            await asyncio.gather(fanout, *writer_tasks)
        finally:
            # Ensure all tasks are cancelled if any error occurs
            for task in writer_tasks:
                if not task.done():
                    task.cancel()
            if not fanout.done():
                fanout.cancel()


async def event_stream_handler[StreamableDepsT: HasStreamWriter](
    run_context: RunContext[StreamableDepsT], events: AsyncIterable[AgentStreamEvent]
) -> None:
    """
    Event stream handler for TemporalAgent.

    Args:
        run_context: The run context for the agent.
        events: An async iterable of AgentStreamEvent objects.

    Returns:
        None

    Raises:
        TypeError: If arguments are not of expected types.
    """
    if not hasattr(events, "__aiter__"):
        raise TypeError("events must be an AsyncIterable")
    logger.info("Run context", run_context=run_context)
    stream_writer = run_context.deps.stream_writer
    try:
        await stream_writer.write(events)
    except Exception as e:
        logger.error("Error writing to stream", error=e)
        raise e
