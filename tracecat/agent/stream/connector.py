from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

import orjson
from fastapi import Request
from pydantic_core import to_jsonable_python

from tracecat.agent.runtime import ModelMessageTA
from tracecat.agent.stream.events import (
    AgentStreamEventTA,
    StreamConnected,
    StreamDelta,
    StreamEnd,
    StreamError,
    StreamEvent,
    StreamFormat,
    StreamMessage,
)
from tracecat.chat import tokens
from tracecat.chat.service import ChatService
from tracecat.logger import logger
from tracecat.redis.client import RedisClient


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
                        count=100,
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
        request: Request,
        last_id: str,
        format: StreamFormat,
    ) -> AsyncIterable[str]:
        match format:
            case "vercel":
                from tracecat.agent.adapter.vercel import sse_vercel

                return sse_vercel(self._stream_events(request, last_id))
            case "basic":
                return self.simple_sse(request, last_id)
            case _:
                raise ValueError(f"Invalid format: {format}")

    async def simple_sse(self, request: Request, last_id: str) -> AsyncIterable[str]:
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
