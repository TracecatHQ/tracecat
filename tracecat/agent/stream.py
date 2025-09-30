from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

import aiohttp
import orjson
import pydantic
from fastapi import Request
from pydantic import TypeAdapter
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
)
from pydantic_ai.tools import RunContext
from pydantic_core import to_jsonable_python

from tracecat.agent.adapter.vercel import adapt_events_to_vercel
from tracecat.agent.runtime import ModelMessageTA
from tracecat.chat import tokens
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


@dataclass(slots=True, kw_only=True)
class StreamDelta:
    """Container for Redis stream payloads and adapter errors."""

    kind: Literal["event"] = "event"
    id: str
    event: AgentStreamEvent

    def sse(self) -> str:
        return f"id: {self.id}\nevent: delta\ndata: {orjson.dumps(self.event).decode()}\n\n"


@dataclass(slots=True, kw_only=True)
class StreamMessage:
    """Container for Redis stream payloads and adapter errors."""

    kind: Literal["message"] = "message"
    id: str
    message: ModelMessage

    def sse(self) -> str:
        return f"id: {self.id}\nevent: message\ndata: {orjson.dumps(self.message).decode()}\n\n"


@dataclass(slots=True, kw_only=True)
class StreamConnected:
    """Container for Redis stream payloads and adapter errors."""

    kind: Literal["connected"] = "connected"
    id: str

    def sse(self) -> str:
        return f"id: {self.id}\nevent: connected\ndata: {{}}\n\n"


@dataclass(slots=True, kw_only=True)
class StreamEnd:
    """Container for Redis stream payloads and adapter errors."""

    kind: Literal["end-of-stream"] = "end-of-stream"
    id: str

    @staticmethod
    def sse() -> str:
        return "event: end\ndata: {}\n\n"


@dataclass(slots=True, kw_only=True)
class StreamError:
    """Container for Redis stream payloads and adapter errors."""

    kind: Literal["error"] = "error"
    error: str

    def sse(self) -> str:
        return f"event: error\ndata: {{'error': '{self.error}'}}\n\n"


type StreamEvent = Annotated[
    StreamDelta | StreamMessage | StreamEnd | StreamError,
    pydantic.Discriminator("kind"),
]


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

    async def _stream_events(
        self, request: Request, last_id: str
    ) -> AsyncIterator[StreamEvent]:
        current_id = last_id
        try:
            while not await request.is_disconnected():
                try:
                    if result := await self.client.xread(
                        streams={self._stream_key: current_id},
                        count=10,
                        block=1000,
                    ):
                        for _stream_name, messages in result:
                            for msg_id, fields in messages:
                                data = orjson.loads(fields[tokens.DATA_KEY])
                                current_id = msg_id
                                match data:
                                    case {tokens.END_TOKEN: tokens.END_TOKEN_VALUE}:
                                        logger.debug("End-of-stream marker")
                                        yield StreamEnd(id=msg_id)
                                    case {"event_kind": event_kind}:
                                        logger.debug(
                                            "Stream event", event_kind=event_kind
                                        )
                                        event = AgentStreamEventTA.validate_python(data)
                                        yield StreamDelta(id=msg_id, event=event)
                                    case {"kind": kind}:
                                        logger.debug("Model message", kind=kind)
                                        message = ModelMessageTA.validate_python(data)
                                        yield StreamMessage(id=msg_id, message=message)
                                    case _:
                                        logger.warning(
                                            "Invalid stream message",
                                            error="Unexpected payload",
                                            message_id=msg_id,
                                        )

                        await self._set_last_stream_id(current_id)

                    await asyncio.sleep(0.1)

                except Exception as e:
                    logger.error("Error reading from Redis stream", error=str(e))
                    yield StreamError(error="Stream read error")
                    await asyncio.sleep(1)

        except Exception as e:
            logger.error("Fatal error in stream generator", error=str(e))
            yield StreamError(error="Fatal stream error")
        finally:
            logger.info("Chat stream ended", stream_key=self._stream_key)
            await self._set_last_stream_id(current_id)

    async def sse(self, request: Request, last_id: str) -> AsyncIterable[str]:
        try:
            yield StreamConnected(id=last_id).sse()
            async for event in self._stream_events(request, last_id):
                match event:
                    case StreamError(error=error):
                        yield event.sse()
                        if error == "Fatal stream error":
                            break
                        continue
                    case StreamEnd():
                        logger.debug("End-of-stream marker")
                        yield event.sse()
                        break
                    case StreamDelta(event=delta):
                        logger.debug("Stream event", event_kind=delta.event_kind)
                        yield event.sse()
                    case StreamMessage(message=message):
                        logger.debug("Model message", kind=message.kind)
                        yield event.sse()
                    case _:
                        logger.warning(
                            "Invalid stream message",
                            error="Unexpected payload",
                            event=event,
                        )
        finally:
            yield StreamEnd.sse()

    async def sse_vercel(self, request: Request, last_id: str) -> AsyncIterable[str]:
        """Stream Redis events as Vercel AI SDK frames without persisting adapter output."""

        try:
            yield StreamConnected(id=last_id).sse()

            unwrapped_stream = unwrap_stream(self._stream_events(request, last_id))
            async for frame in adapt_events_to_vercel(unwrapped_stream):
                yield frame

            yield StreamEnd.sse()

        except Exception as e:
            logger.error("Error in Vercel SSE stream", error=str(e))
            yield StreamError(error="Stream error").sse()


async def unwrap_stream(
    stream: AsyncIterable[StreamEvent],
) -> AsyncIterator[AgentStreamEvent | ModelMessage]:
    """Unwrap StreamEvent containers to extract actual events/messages."""
    async for item in stream:
        match item:
            case StreamDelta(event=event):
                yield event
            case StreamMessage(message=message):
                yield message
            case StreamEnd() | StreamError():
                break


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
