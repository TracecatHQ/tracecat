from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp
import orjson
from fastapi import Request
from pydantic import TypeAdapter
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
)
from pydantic_ai.tools import RunContext
from pydantic_core import to_jsonable_python

from tracecat.agent.runtime import ModelMessageTA
from tracecat.chat import tokens

# Late import to avoid circular dependency
from tracecat.chat.service import ChatService
from tracecat.logger import logger
from tracecat.redis.client import RedisClient

ta: TypeAdapter[AgentStreamEvent] = TypeAdapter(AgentStreamEvent)


class StreamWriter(Protocol):
    async def write(self, events: AsyncIterable[AgentStreamEvent]) -> None: ...


class HasStreamWriter(Protocol):
    stream_writer: StreamWriter


@dataclass
class BasicStreamingAgentDeps:
    stream_writer: StreamWriter

    async def store(self, events: AgentStreamEvent) -> None: ...


AgentStreamEventTA: TypeAdapter[AgentStreamEvent] = TypeAdapter(AgentStreamEvent)


class AgentStream:
    def __init__(self, client: RedisClient, session_id: uuid.UUID):
        self.client = client
        self.session_id = session_id
        self._stream_key = f"agent-stream:{str(self.session_id)}"

    async def append(self, event: Any) -> None:
        """Stream a message to a Redis stream."""
        await self.client.xadd(
            self._stream_key,
            {tokens.DATA_KEY: orjson.dumps(event, default=to_jsonable_python).decode()},
            maxlen=10000,
            approximate=True,
        )

    async def done(self) -> None:
        logger.debug("Adding end-of-stream marker", stream_key=self._stream_key)
        await self.append({tokens.END_TOKEN: tokens.END_TOKEN_VALUE})

    async def _set_last_stream_id(self, last_stream_id: str) -> None:
        async with ChatService.with_session() as chat_svc:
            if chat := await chat_svc.get_chat(self.session_id):
                chat.last_stream_id = last_stream_id
                await chat_svc.update_chat(chat)
                logger.info(
                    "Updated chat with last stream id",
                    chat_id=chat.id,
                    last_stream_id=last_stream_id,
                )
            else:
                logger.warning("Chat not found", session_id=self.session_id)

    async def sse(self, request: Request, last_id: str) -> AsyncIterable[str]:
        try:
            # Send initial connection event
            yield f"id: {last_id}\nevent: connected\ndata: {{}}\n\n"

            current_id = last_id

            while not await request.is_disconnected():
                try:
                    # Read from Redis stream with blocking
                    if result := await self.client.xread(
                        streams={self._stream_key: current_id},
                        count=10,
                        block=1000,  # Block for 1 second
                    ):
                        for _stream_name, messages in result:
                            for message_id, fields in messages:
                                try:
                                    data = orjson.loads(fields[tokens.DATA_KEY])
                                    logger.debug("Stream message", data=data)
                                    if not isinstance(data, dict):
                                        raise ValueError(
                                            f"Invalid stream message, expected dict but got {type(data)}"
                                        )

                                    # Check for end-of-stream marker
                                    match data:
                                        case {tokens.END_TOKEN: tokens.END_TOKEN_VALUE}:
                                            logger.debug("End-of-stream marker")
                                            yield f"id: {message_id}\nevent: end\ndata: {{}}\n\n"
                                        case {"event_kind": event_kind}:
                                            logger.debug(
                                                "Stream event", event_kind=event_kind
                                            )
                                            event = AgentStreamEventTA.validate_python(
                                                data
                                            )
                                            data_json = orjson.dumps(event).decode()
                                            yield f"id: {message_id}\nevent: delta\ndata: {data_json}\n\n"
                                        case {"kind": kind}:
                                            logger.debug("Model message", kind=kind)
                                            message = ModelMessageTA.validate_python(
                                                data
                                            )
                                            data_json = orjson.dumps(message).decode()
                                            yield f"id: {message_id}\nevent: message\ndata: {data_json}\n\n"
                                        case _:
                                            raise ValueError(
                                                f"Invalid stream message, expected dict but got {type(data)}"
                                            )

                                    # Ensure in all cases we advance the current ID
                                    current_id = message_id

                                except Exception as e:
                                    logger.warning(
                                        "Failed to process stream message",
                                        error=str(e),
                                        message_id=message_id,
                                    )
                                    continue
                            # Store this every len(messages) messages
                            await self._set_last_stream_id(current_id)

                    # Send heartbeat to keep connection alive
                    await asyncio.sleep(0.1)

                except Exception as e:
                    logger.error("Error reading from Redis stream", error=str(e))
                    yield 'event: error\ndata: {"error": "Stream read error"}\n\n'
                    await asyncio.sleep(1)

        except Exception as e:
            logger.error("Fatal error in stream generator", error=str(e))
            yield 'event: error\ndata: {"error": "Fatal stream error"}\n\n'
        finally:
            logger.info("Chat stream ended", stream_key=self._stream_key)
            await self._set_last_stream_id(current_id)
            yield "event: end\ndata: {{}}\n\n"


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


class PersistentStreamWriter(StreamWriter):
    def __init__(self, stream: AgentStream, chat_id: uuid.UUID):
        self.stream = stream
        self.chat_id = chat_id

    async def write(self, events: AsyncIterable[AgentStreamEvent]) -> None:
        async for event in events:
            await self.stream.append(event)
        await self.stream.done()

    async def store(self, messages: list[ModelMessage]) -> None:
        async with ChatService.with_session() as chat_svc:
            await chat_svc.append_messages(self.chat_id, messages)
