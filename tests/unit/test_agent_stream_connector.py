from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamDelta, StreamEnd
from tracecat.chat import tokens
from tracecat.redis.client import RedisClient


@pytest.mark.anyio
async def test_stream_events_clears_buffer_after_terminal_marker() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    client = SimpleNamespace(
        xread=AsyncMock(
            return_value=[
                (
                    f"agent-stream:{workspace_id}:{session_id}",
                    [
                        (
                            "1717426372769-0",
                            {
                                tokens.DATA_KEY: b'{"[TURN_END]":1}',
                            },
                        )
                    ],
                )
            ]
        ),
        delete=AsyncMock(return_value=1),
    )
    stream = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )

    stream.clear = AsyncMock()
    stream._set_last_stream_id = AsyncMock()

    events = [
        event
        async for event in stream._stream_events(
            AsyncMock(side_effect=[False, True]), last_id="0-0"
        )
    ]
    event = events[0]

    assert isinstance(event, StreamEnd)

    stream.clear.assert_awaited_once()
    stream._set_last_stream_id.assert_not_awaited()


@pytest.mark.anyio
async def test_stream_events_preserves_cursor_when_stream_not_completed() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    client = SimpleNamespace(
        xread=AsyncMock(
            return_value=[
                (
                    f"agent-stream:{workspace_id}:{session_id}",
                    [
                        (
                            "1717426372768-0",
                            {
                                tokens.DATA_KEY: b'{"type":"text_delta","text":"hello"}',
                            },
                        )
                    ],
                )
            ]
        ),
        delete=AsyncMock(return_value=1),
    )
    stream = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )

    stop_condition = AsyncMock(side_effect=[False, True])
    stream.clear = AsyncMock()
    stream._set_last_stream_id = AsyncMock()

    events = [
        event async for event in stream._stream_events(stop_condition, last_id="0-0")
    ]

    assert len(events) == 1
    assert isinstance(events[0], StreamDelta)
    stream.clear.assert_not_awaited()
    stream._set_last_stream_id.assert_awaited()
