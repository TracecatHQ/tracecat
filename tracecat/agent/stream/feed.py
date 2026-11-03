"""Per-session feed of run state changes, for discovery by open web tabs.

The feed carries state only: no authors, message bodies, or tokens. The session
row (``curr_run_id``, ``active_stream_id``) stays the source of truth, so
publishing is best effort and readers start from a row snapshot.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Literal

import orjson

from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamKeepAlive
from tracecat.chat import tokens
from tracecat.logger import logger
from tracecat.redis.client import RedisClient, get_redis_client

FEED_MAXLEN = 100
FEED_TTL_SECONDS = 24 * 60 * 60
FEED_POLL_INTERVAL_SECONDS = 1.0
# Under the 30s default stop grace on ECS and Kubernetes, so an open tab never
# holds up a deploy or a dev reload. EventSource reconnects with a fresh snapshot.
FEED_MAX_CONNECTION_SECONDS = 25
FEED_RECONNECT_DELAY_MS = 1000
PUBLISH_TIMEOUT_SECONDS = 2


class SessionFeedEventType(StrEnum):
    RUN_STARTED = "run.started"
    RUN_PAUSED = "run.paused"
    RUN_RESUMED = "run.resumed"
    RUN_FINISHED = "run.finished"


@dataclass(frozen=True, slots=True)
class SessionFeedEvent:
    """One run state change. ``stream_id`` names the segment to attach to."""

    type: SessionFeedEventType
    run_id: uuid.UUID
    stream_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class SessionFeedSnapshot:
    """Session row state sent first on every (re)connect."""

    run_id: uuid.UUID | None
    stream_id: uuid.UUID | None
    paused: bool
    type: Literal["snapshot"] = "snapshot"


def session_feed_key(workspace_id: uuid.UUID, session_id: uuid.UUID) -> str:
    return f"agent-feed:{workspace_id}:{session_id}"


def _sse_data(data: str) -> str:
    return f"data: {data}\n\n"


def _dumps(value: SessionFeedEvent | SessionFeedSnapshot) -> str:
    # Row values are asyncpg UUIDs, which orjson only serializes via `default`.
    return orjson.dumps(value, default=str).decode()


class SessionFeed:
    """Redis stream holding one session's run state changes."""

    def __init__(
        self,
        *,
        client: RedisClient,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID,
    ):
        self.client = client
        self.key = session_feed_key(workspace_id, session_id)

    @classmethod
    async def new(
        cls, *, workspace_id: uuid.UUID, session_id: uuid.UUID
    ) -> SessionFeed:
        client = await get_redis_client()
        return cls(client=client, workspace_id=workspace_id, session_id=session_id)

    async def publish(self, event: SessionFeedEvent) -> None:
        await self.client.xadd(
            self.key,
            {tokens.DATA_KEY: _dumps(event)},
            maxlen=FEED_MAXLEN,
            approximate=True,
            expire_seconds=FEED_TTL_SECONDS,
        )

    async def latest_id(self) -> str:
        """Return the newest entry id, or ``0-0`` for an empty feed."""
        entries = await self.client.xrevrange(self.key, count=1)
        return entries[0][0] if entries else "0-0"

    async def sse(
        self,
        snapshot: SessionFeedSnapshot,
        after_id: str,
        stop_condition: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[str]:
        """Yield the snapshot, then entries after ``after_id`` as SSE frames.

        Ends after ``FEED_MAX_CONNECTION_SECONDS``; the client reconnects.
        """
        yield f"retry: {FEED_RECONNECT_DELAY_MS}\n" + _sse_data(_dumps(snapshot))
        current_id = after_id
        opened_at = last_sent_at = monotonic()
        while not await stop_condition():
            if monotonic() - opened_at >= FEED_MAX_CONNECTION_SECONDS:
                return
            try:
                # Non-blocking: a blocking read pins a pooled connection per tab.
                result = await self.client.xread(
                    streams={self.key: current_id}, count=100
                )
            except Exception as e:
                logger.error("Error reading session feed", key=self.key, error=str(e))
                result = []
            for _key, entries in result or []:
                for entry_id, fields in entries:
                    current_id = entry_id
                    if (data := fields.get(tokens.DATA_KEY)) is not None:
                        last_sent_at = monotonic()
                        yield _sse_data(data)
            if monotonic() - last_sent_at >= AgentStream.KEEPALIVE_INTERVAL_SECONDS:
                last_sent_at = monotonic()
                yield StreamKeepAlive.sse()
            await asyncio.sleep(FEED_POLL_INTERVAL_SECONDS)


async def publish_session_event(
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    type: SessionFeedEventType,
    run_id: uuid.UUID,
    stream_id: uuid.UUID | None = None,
) -> None:
    """Publish a run state change; failures are logged, never raised."""
    event = SessionFeedEvent(type=type, run_id=run_id, stream_id=stream_id)
    try:
        async with asyncio.timeout(PUBLISH_TIMEOUT_SECONDS):
            feed = await SessionFeed.new(
                workspace_id=workspace_id, session_id=session_id
            )
            await feed.publish(event)
    except Exception as e:
        logger.warning(
            "Failed to publish session feed event",
            session_id=str(session_id),
            run_id=str(run_id),
            event_type=type.value,
            error=str(e),
        )
