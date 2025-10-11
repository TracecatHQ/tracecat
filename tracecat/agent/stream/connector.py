from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from time import monotonic
from typing import Any

import orjson
from pydantic_core import to_jsonable_python

from tracecat.agent.models import ModelMessageTA
from tracecat.agent.stream.events import (
    AgentStreamEventTA,
    StreamConnected,
    StreamDelta,
    StreamEnd,
    StreamError,
    StreamEvent,
    StreamFormat,
    StreamKeepAlive,
    StreamMessage,
)
from tracecat.agent.types import StreamKey
from tracecat.chat import tokens
from tracecat.chat.service import ChatService
from tracecat.logger import logger
from tracecat.redis.client import RedisClient, get_redis_client


class AgentStream:
    """Stream adapter backed by Redis streams."""

    KEEPALIVE_INTERVAL_SECONDS = 10

    def __init__(
        self,
        client: RedisClient,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID,
        *,
        namespace: str = "agent",
    ):
        self.client = client
        self.workspace_id = workspace_id
        self.session_id = session_id
        self.namespace = namespace
        self._stream_key = StreamKey(workspace_id, session_id, namespace=namespace)

    @classmethod
    async def new(
        cls, session_id: uuid.UUID, workspace_id: uuid.UUID, *, namespace: str = "agent"
    ) -> AgentStream:
        client = await get_redis_client()
        return cls(client, workspace_id, session_id, namespace=namespace)

    async def append(self, event: Any) -> None:
        """Stream a message to a Redis stream."""
        await self.client.xadd(
            self._stream_key,
            {tokens.DATA_KEY: orjson.dumps(event, default=to_jsonable_python).decode()},
            maxlen=10000,
            approximate=True,
        )

    async def error(self, error: str) -> None:
        """Emit an error marker."""
        logger.debug("Adding error marker", stream_key=self._stream_key)
        await self.append({"kind": "error", "error": error})

    async def done(self) -> None:
        """Emit an end-of-turn marker."""
        logger.debug("Adding end-of-turn marker", stream_key=self._stream_key)
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
                # This is expected if we are streaming a session that
                # was not created from a chat.
                logger.debug("Chat not found", session_id=self.session_id)

    async def _stream_events(
        self, stop_condition: Callable[[], Awaitable[bool]], last_id: str
    ) -> AsyncIterator[StreamEvent]:
        """Stream events from Redis until a stop condition is met.

        Continuously reads messages from the Redis stream and yields them as
        StreamEvent objects. Handles different event types including agent events,
        model messages, errors, and end-of-stream markers.

        Args:
            stop_condition: Async callable that returns True when streaming should stop
                          (e.g., when client disconnects).
            last_id: The Redis stream ID to start reading from. Use "0-0" to read
                    from the beginning, or a specific ID to resume from that point.

        Yields:
            StreamEvent: One of StreamDelta (agent events), StreamMessage (model messages),
                        StreamError (error events), or StreamEnd (end-of-stream marker).

        Note:
            - Periodically updates the chat's last_stream_id for reconnection support
            - Implements exponential backoff on errors (1s sleep)
            - Blocks for up to 1 second waiting for new messages
            - Processes up to 100 messages per read operation
        """
        current_id = last_id
        last_keepalive = monotonic()
        try:
            while not await stop_condition():
                try:
                    if result := await self.client.xread(
                        streams={self._stream_key: current_id},
                        count=100,
                        block=1000,
                    ):
                        last_keepalive = monotonic()
                        for _stream_name, messages in result:
                            for msg_id, fields in messages:
                                data = orjson.loads(fields[tokens.DATA_KEY])
                                current_id = msg_id
                                match data:
                                    case {tokens.END_TOKEN: tokens.END_TOKEN_VALUE}:
                                        logger.debug("End-of-stream marker")
                                        yield StreamEnd(id=msg_id)
                                    case {"event_kind": event_kind}:
                                        event = AgentStreamEventTA.validate_python(data)
                                        logger.debug(
                                            "Stream event", kind=event_kind, event=event
                                        )
                                        yield StreamDelta(id=msg_id, event=event)
                                    case {"kind": "error", "error": error_message}:
                                        logger.warning(
                                            "Stream error received",
                                            error=error_message,
                                            message_id=msg_id,
                                        )
                                        yield StreamError(error=error_message)
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

                    now = monotonic()
                    if now - last_keepalive >= self.KEEPALIVE_INTERVAL_SECONDS:
                        logger.debug(
                            "Emitting keep-alive event",
                            stream_key=self._stream_key,
                        )
                        yield StreamKeepAlive()
                        last_keepalive = now

                    await asyncio.sleep(0)

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

    def sse(
        self,
        stop_condition: Callable[[], Awaitable[bool]],
        last_id: str,
        format: StreamFormat,
    ) -> AsyncIterable[str]:
        match format:
            case "vercel":
                from tracecat.agent.adapter.vercel import sse_vercel

                return sse_vercel(self._stream_events(stop_condition, last_id))
            case "basic":
                return self.simple_sse(stop_condition, last_id)
            case _:
                raise ValueError(f"Invalid format: {format}")

    async def simple_sse(
        self, stop_condition: Callable[[], Awaitable[bool]], last_id: str
    ) -> AsyncIterable[str]:
        try:
            yield StreamConnected(id=last_id).sse()
            async for event in self._stream_events(stop_condition, last_id):
                match event:
                    case StreamKeepAlive():
                        yield event.sse()
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
