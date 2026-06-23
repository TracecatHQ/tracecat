from __future__ import annotations

import asyncio
import uuid
from collections.abc import (
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from time import monotonic
from typing import Any

import orjson
from pydantic_core import to_jsonable_python

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
    UnifiedStreamEventTA,
    parse_vercel_frame_cursor,
)
from tracecat.agent.types import ModelMessageTA, StreamKey
from tracecat.chat import tokens
from tracecat.logger import logger
from tracecat.redis.client import RedisClient, get_redis_client


class AgentStream:
    """Stream adapter backed by Redis streams.

    Each turn uses a per-turn Redis key suffixed by ``stream_id`` (the session's
    ``active_stream_id``). Old readers harmlessly drain a dead key; a new turn
    never collides with a prior turn's buffer. Readers are read-only and never
    write ``last_stream_id`` - the browser owns the reconnect cursor.
    """

    KEEPALIVE_INTERVAL_SECONDS = 10
    COMPLETED_STREAM_TTL_SECONDS = 5 * 60

    def __init__(
        self,
        client: RedisClient,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID,
        stream_id: uuid.UUID | None = None,
    ):
        self.client = client
        self.workspace_id = workspace_id
        self.session_id = session_id
        self.stream_id = stream_id
        self._stream_key = StreamKey(workspace_id, session_id, stream_id)

    @classmethod
    async def new(
        cls,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID,
        stream_id: uuid.UUID | None = None,
    ) -> AgentStream:
        client = await get_redis_client()
        return cls(client, workspace_id, session_id, stream_id)

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
        await self.append({"kind": "error", "error": error})

    async def done(self) -> None:
        """Emit an end-of-turn marker."""
        await self.append({tokens.END_TOKEN: tokens.END_TOKEN_VALUE})

    async def clear_buffer(self) -> None:
        """Delete the stream buffer for this key.

        Used by workflow-initiated (ai.*) sessions that share a per-session key
        and must clear any stale buffer before a new run so GET /stream replays
        only the new run. Chat turns instead use a fresh per-turn key and never
        reuse a prior buffer.
        """
        await self.client.delete(self._stream_key)

    async def min_entry_id(self) -> str | None:
        """Oldest id still in the live buffer, or None if empty/evicted.

        Used for reconnect gap detection: a client cursor older than this was
        trimmed (maxlen) or TTL-evicted, so it cannot be resumed.
        """
        entries = await self.client.xrange(self._stream_key, count=1)
        return entries[0][0] if entries else None

    async def _expire_completed_stream(self) -> None:
        """Keep completed streams briefly for reconnects, then let Redis evict them."""
        try:
            redis_client = await self.client._get_client()
            await redis_client.expire(
                name=self._stream_key,
                time=self.COMPLETED_STREAM_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning(
                "Failed to shorten completed stream TTL",
                stream_key=self._stream_key,
                error=str(exc),
            )

    async def _stream_events(
        self,
        stop_condition: Callable[[], Awaitable[bool]],
        last_id: str,
        *,
        include_last_id: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Stream events from Redis until a stop condition is met.

        Args:
            stop_condition: Async callable that returns True when streaming should
                stop (e.g., when client disconnects).
            last_id: The Redis stream ID to start reading from. Use "0-0" to read
                from the beginning, or a specific ID to resume from that point.
            include_last_id: When resuming from a mid-entry frame cursor, also
                re-emit the entry at ``last_id`` so the adapter can re-fan its
                frames; the adapter then drops frames already seen by the client.

        Yields:
            StreamEvent: StreamDelta, StreamMessage, StreamError, or StreamEnd.

        Note:
            - Read-only: never writes last_stream_id (browser owns the cursor).
            - Blocks for up to 1 second waiting for new messages.
            - Processes up to 100 messages per read operation.
        """
        current_id = last_id
        last_keepalive = monotonic()
        stream_completed = False

        async def parse_stream_messages(
            messages: Sequence[tuple[str, Mapping[str, str]]],
        ) -> AsyncIterator[StreamEvent]:
            nonlocal current_id, stream_completed
            for msg_id, fields in messages:
                data = orjson.loads(fields[tokens.DATA_KEY])
                current_id = msg_id
                match data:
                    case {tokens.END_TOKEN: tokens.END_TOKEN_VALUE}:
                        stream_completed = True
                        yield StreamEnd(id=msg_id)
                    case {"event_kind": _}:
                        legacy_event = AgentStreamEventTA.validate_python(data)
                        yield StreamDelta(id=msg_id, event=legacy_event)
                    case {"type": _}:
                        unified_event = UnifiedStreamEventTA.validate_python(data)
                        yield StreamDelta(id=msg_id, event=unified_event)
                    case {"kind": "error", "error": error_message}:
                        logger.warning(
                            "Stream error received",
                            error=error_message,
                            message_id=msg_id,
                        )
                        yield StreamError(error=error_message)
                    case {"kind": _}:
                        message = ModelMessageTA.validate_python(data)
                        yield StreamMessage(id=msg_id, message=message)
                    case _:
                        logger.warning(
                            "Invalid stream message",
                            error="Unexpected payload",
                            message_id=msg_id,
                        )

        try:
            if include_last_id and current_id != "0-0":
                try:
                    entries = await self.client.xrange(
                        self._stream_key,
                        min_id=current_id,
                        max_id=current_id,
                        count=1,
                    )
                    async for event in parse_stream_messages(entries):
                        yield event
                except Exception as e:
                    logger.error("Error reading Redis cursor entry", error=str(e))
                    yield StreamError(error="Stream read error")

            while not await stop_condition():
                try:
                    if result := await self.client.xread(
                        streams={self._stream_key: current_id},
                        count=100,
                        block=1000,
                    ):
                        last_keepalive = monotonic()
                        for _stream_name, messages in result:
                            async for event in parse_stream_messages(messages):
                                yield event

                    now = monotonic()
                    if now - last_keepalive >= self.KEEPALIVE_INTERVAL_SECONDS:
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
            # Readers never write last_stream_id; the browser owns the reconnect
            # cursor (Last-Event-ID). We only expire the buffer after terminal.
            if stream_completed:
                await self._expire_completed_stream()

    async def stream_events(
        self,
        stop_condition: Callable[[], Awaitable[bool]],
        last_id: str,
        *,
        include_last_id: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Public stream-events iterator for external stream consumers."""
        async for event in self._stream_events(
            stop_condition,
            last_id,
            include_last_id=include_last_id,
        ):
            yield event

    def sse(
        self,
        stop_condition: Callable[[], Awaitable[bool]],
        last_id: str,
        format: StreamFormat,
        *,
        message_id: str | None = None,
        resume_from: str | None = None,
    ) -> AsyncIterable[str]:
        cursor = parse_vercel_frame_cursor(resume_from)
        # Strip any composite ``<redis-id>:<frame-index>`` suffix so Redis XREAD
        # gets a plain id. No-op for plain ids / "0-0" (parse returns None).
        last_id_cursor = parse_vercel_frame_cursor(last_id)
        if last_id_cursor is not None:
            last_id = last_id_cursor.redis_id
        # Legacy callers reconnect with the composite Last-Event-ID in ``last_id``
        # and no explicit ``resume_from``. Treat that composite as the resume
        # cursor: re-emit the cursor entry (include_last_id) and forward it to the
        # adapter so its dedup drops only frames the client already saw. Without
        # this the entry's remaining frames are skipped (XREAD is exclusive).
        if cursor is not None:
            effective_cursor = cursor
            effective_resume_from = resume_from
        elif last_id_cursor is not None:
            # ``last_id`` was reassigned to the bare redis id above; rebuild the
            # composite to forward as the resume cursor.
            effective_cursor = last_id_cursor
            effective_resume_from = (
                f"{last_id_cursor.redis_id}:{last_id_cursor.frame_index}"
            )
        else:
            effective_cursor = None
            effective_resume_from = resume_from
        match format:
            case "vercel":
                from tracecat.agent.adapter.vercel import sse_vercel

                return sse_vercel(
                    self.stream_events(
                        stop_condition,
                        last_id,
                        include_last_id=effective_cursor is not None,
                    ),
                    message_id=message_id,
                    resume_from=effective_resume_from,
                )
            case "basic":
                return self.simple_sse(stop_condition, last_id)
            case _:
                raise ValueError(f"Invalid format: {format}")

    def finished_sse(
        self, format: StreamFormat, *, message_id: str | None
    ) -> AsyncIterable[str]:
        """Emit an immediately-finishing stream (no live content).

        Used on reconnect when the cursor is stale and the turn is already
        terminal: the client gets a clean finish and refetches DB history.
        """

        async def _empty() -> AsyncIterator[StreamEvent]:
            return
            yield  # pragma: no cover - establishes async generator

        match format:
            case "vercel":
                from tracecat.agent.adapter.vercel import sse_vercel

                return sse_vercel(_empty(), message_id=message_id)
            case "basic":

                async def _end() -> AsyncIterable[str]:
                    yield StreamEnd.sse()

                return _end()
            case _:
                raise ValueError(f"Invalid format: {format}")

    async def simple_sse(
        self, stop_condition: Callable[[], Awaitable[bool]], last_id: str
    ) -> AsyncIterable[str]:
        try:
            yield StreamConnected(id=last_id).sse()
            async for event in self.stream_events(stop_condition, last_id):
                match event:
                    case StreamKeepAlive():
                        yield event.sse()
                    case StreamError(error=error):
                        yield event.sse()
                        if error == "Fatal stream error":
                            break
                        continue
                    case StreamEnd():
                        yield event.sse()
                        break
                    case StreamDelta():
                        yield event.sse()
                    case StreamMessage():
                        yield event.sse()
                    case _:
                        logger.warning(
                            "Invalid stream message",
                            error="Unexpected payload",
                            event=event,
                        )
        finally:
            yield StreamEnd.sse()
