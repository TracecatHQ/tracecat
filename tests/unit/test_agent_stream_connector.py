from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamDelta, StreamEnd, StreamKeepAlive
from tracecat.chat import tokens
from tracecat.redis.client import RedisClient


@pytest.mark.anyio
async def test_per_turn_stream_key_includes_stream_id() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    stream_id = uuid.uuid4()
    client = SimpleNamespace(delete=AsyncMock(return_value=1))

    per_turn = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
        stream_id=stream_id,
    )
    per_session = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )

    assert per_turn._stream_key == (
        f"agent-stream:{workspace_id}:{session_id}:{stream_id}"
    )
    assert per_session._stream_key == f"agent-stream:{workspace_id}:{session_id}"
    # A new turn's key never collides with a prior turn's key.
    assert per_turn._stream_key != per_session._stream_key


@pytest.mark.anyio
async def test_clear_buffer_deletes_key_without_cursor_write() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    client = SimpleNamespace(delete=AsyncMock(return_value=1))
    stream = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )

    await stream.clear_buffer()

    client.delete.assert_awaited_once_with(stream._stream_key)


@pytest.mark.anyio
async def test_min_entry_id_returns_oldest_or_none() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    client = SimpleNamespace(
        xrange=AsyncMock(return_value=[("1717426372768-0", {})]),
    )
    stream = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )

    assert await stream.min_entry_id() == "1717426372768-0"

    client.xrange = AsyncMock(return_value=[])
    assert await stream.min_entry_id() is None


@pytest.mark.anyio
async def test_approval_continuation_marker_tracks_open_stream() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    marker = {tokens.DATA_KEY: b'{"kind":"approval-continuation-start"}'}
    client = SimpleNamespace(
        xadd=AsyncMock(return_value="1-0"),
        xrange=AsyncMock(return_value=[("1-0", marker)]),
        xrevrange=AsyncMock(return_value=[("1-0", marker)]),
    )
    stream = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )

    await stream.mark_approval_continuation()

    assert await stream.is_open_approval_continuation() is True
    client.xadd.assert_awaited_once()

    client.xrevrange = AsyncMock(
        return_value=[("2-0", {tokens.DATA_KEY: b'{"[TURN_END]":1}'})]
    )
    assert await stream.is_open_approval_continuation() is False


@pytest.mark.anyio
async def test_stream_events_consumes_approval_continuation_marker() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    client = SimpleNamespace(
        xread=AsyncMock(
            return_value=[
                (
                    f"agent-stream:{workspace_id}:{session_id}",
                    [
                        (
                            "1-0",
                            {
                                tokens.DATA_KEY: b'{"kind":"approval-continuation-start"}'
                            },
                        )
                    ],
                )
            ]
        )
    )
    stream = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )

    events = [
        event
        async for event in stream._stream_events(
            AsyncMock(side_effect=[False, True]), last_id="0-0"
        )
    ]

    assert len(events) == 1
    assert isinstance(events[0], StreamKeepAlive)


@pytest.mark.anyio
async def test_stream_events_yields_artifact_unified_event() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    raw_client = SimpleNamespace(expire=AsyncMock(return_value=None))
    client = SimpleNamespace(
        xread=AsyncMock(
            return_value=[
                (
                    f"agent-stream:{workspace_id}:{session_id}",
                    [
                        (
                            "1717426372768-0",
                            {
                                tokens.DATA_KEY: (
                                    b'{"type":"artifact","artifact_data":{"op":"upsert",'
                                    b'"artifact":{"type":"generic","id":"g1",'
                                    b'"title":"Result"}}}'
                                ),
                            },
                        )
                    ],
                )
            ]
        ),
        delete=AsyncMock(return_value=1),
        _get_client=AsyncMock(return_value=raw_client),
    )
    stream = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )

    events = [
        event
        async for event in stream._stream_events(
            AsyncMock(side_effect=[False, True]), last_id="0-0"
        )
    ]

    assert len(events) == 1
    assert isinstance(events[0], StreamDelta)
    event = events[0].event
    assert isinstance(event, UnifiedStreamEvent)
    assert event.type is StreamEventType.ARTIFACT
    assert event.artifact_data is not None
    assert event.artifact_data.op == "upsert"
    assert event.artifact_data.artifact == {
        "type": "generic",
        "id": "g1",
        "title": "Result",
    }


@pytest.mark.anyio
async def test_stream_events_expires_buffer_after_terminal_marker() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    raw_client = SimpleNamespace(expire=AsyncMock(return_value=None))
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
        _get_client=AsyncMock(return_value=raw_client),
    )
    stream = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )

    events = [
        event
        async for event in stream._stream_events(
            AsyncMock(side_effect=[False, True]), last_id="0-0"
        )
    ]
    event = events[0]

    assert isinstance(event, StreamEnd)

    # Readers never write last_stream_id; terminal only shortens the buffer TTL.
    raw_client.expire.assert_awaited_once_with(
        name=stream._stream_key,
        time=stream.COMPLETED_STREAM_TTL_SECONDS,
    )


@pytest.mark.anyio
async def test_stream_events_does_not_expire_when_not_completed() -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    raw_client = SimpleNamespace(expire=AsyncMock(return_value=None))
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
        _get_client=AsyncMock(return_value=raw_client),
    )
    stream = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )

    stop_condition = AsyncMock(side_effect=[False, True])

    events = [
        event async for event in stream._stream_events(stop_condition, last_id="0-0")
    ]

    assert len(events) == 1
    assert isinstance(events[0], StreamDelta)
    raw_client.expire.assert_not_awaited()
