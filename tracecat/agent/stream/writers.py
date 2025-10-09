from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlparse

import aiohttp
from pydantic_ai.messages import AgentStreamEvent
from pydantic_ai.tools import RunContext

from tracecat.agent.stream.events import AgentStreamEventTA
from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.agent.stream.connector import AgentStream


class StreamWriter(Protocol):
    stream: AgentStream

    async def write(self, events: AsyncIterable[AgentStreamEvent]) -> None: ...


class HasStreamWriter(Protocol):
    stream_writer: StreamWriter


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
    ctx: RunContext[StreamableDepsT], events: AsyncIterable[AgentStreamEvent]
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
    stream_writer = ctx.deps.stream_writer
    try:
        await stream_writer.write(events)
    except Exception as e:
        logger.error("Error writing to stream", error=e)
        raise e


@dataclass
class AgentStreamWriter:
    stream: AgentStream

    async def write(self, events: AsyncIterable[AgentStreamEvent]) -> None:
        async for event in events:
            await self.stream.append(event)


class HttpStreamWriter(StreamWriter):
    def __init__(self, url: str = "https://localhost:1234/stream"):
        self.url = url
        self._ensure_secure_url()

    def _ensure_secure_url(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme != "https":
            raise ValueError("HttpStreamWriter requires an HTTPS endpoint")

    async def write(self, events: AsyncIterable[AgentStreamEvent]) -> None:
        self._ensure_secure_url()
        async with aiohttp.ClientSession() as session:
            async for event in events:
                logger.warning("STREAM EVENT", event=event)
                # Make a post request to the API to stream the event
                async with session.post(
                    self.url,
                    json={"event": AgentStreamEventTA.dump_json(event).decode()},
                ) as response:
                    logger.warning("STREAM RESPONSE", response=response.status)
