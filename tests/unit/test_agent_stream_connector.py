from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import orjson
import pytest

from tracecat.agent.common.stream_types import StreamEventType, UnifiedStreamEvent
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import (
    StreamDelta,
    StreamEnd,
    StreamKeepAlive,
    StreamSessionEvent,
)
from tracecat.chat import tokens
from tracecat.redis.client import RedisClient


async def _empty_event_stream() -> AsyncIterator[None]:
    if False:
        yield


def test_sse_vercel_format_uses_vercel_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    client = SimpleNamespace()
    stream = AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )
    expected_stream = _empty_event_stream()
    adapter = Mock(return_value=expected_stream)

    monkeypatch.setattr("tracecat.agent.adapter.vercel.sse_vercel", adapter)

    result = stream.sse(
        AsyncMock(return_value=False),
        last_id="0-0",
        format="vercel",
    )

    assert result is expected_stream
    adapter.assert_called_once()


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
    previous_stream_id = uuid.uuid4()
    marker = {
        tokens.DATA_KEY: orjson.dumps(
            {
                "kind": "approval-continuation-start",
                "submission_key": "approval-key",
                "previous_stream_id": str(previous_stream_id),
            }
        )
    }
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

    await stream.mark_approval_continuation(
        submission_key="approval-key",
        previous_stream_id=previous_stream_id,
    )

    assert await stream.is_open_approval_continuation() is True
    parsed_marker = await stream.approval_continuation_marker()
    assert parsed_marker is not None
    assert parsed_marker.previous_stream_id == previous_stream_id
    assert parsed_marker.submission_key == "approval-key"
    client.xadd.assert_awaited_once()
    written_marker = orjson.loads(client.xadd.await_args.args[1][tokens.DATA_KEY])
    assert written_marker == {
        "kind": "approval-continuation-start",
        "submission_key": "approval-key",
        "previous_stream_id": str(previous_stream_id),
    }

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
async def test_stream_events_yields_nonterminal_idle_boundary_without_expiring() -> (
    None
):
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
                                tokens.DATA_KEY: (
                                    b'{"[TURN_END]":1,'
                                    b'"terminal":false,'
                                    b'"reason":"approval_pending"}'
                                ),
                            },
                        )
                    ],
                )
            ]
        ),
        xrange=AsyncMock(return_value=[]),
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
    assert isinstance(events[0], StreamEnd)
    raw_client.expire.assert_not_awaited()


@pytest.mark.anyio
async def test_stream_events_skips_stale_nonterminal_idle_boundary() -> None:
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
                                tokens.DATA_KEY: (
                                    b'{"[TURN_END]":1,'
                                    b'"terminal":false,'
                                    b'"reason":"approval_pending"}'
                                ),
                            },
                        ),
                        (
                            "1717426372770-0",
                            {
                                tokens.DATA_KEY: b'{"type":"text_delta","text":"next"}',
                            },
                        ),
                    ],
                )
            ]
        ),
        xrange=AsyncMock(return_value=[("1717426372770-0", {})]),
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
    client.xrange.assert_awaited_once_with(
        stream._stream_key,
        min_id="(1717426372769-0",
        count=1,
    )
    raw_client.expire.assert_not_awaited()


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


def _stream_with_entries(
    entries: list[tuple[str, dict[str, object]]],
) -> AgentStream:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    client = SimpleNamespace(
        xread=AsyncMock(
            return_value=[
                (
                    f"agent-stream:{workspace_id}:{session_id}",
                    [
                        (msg_id, {tokens.DATA_KEY: orjson.dumps(payload)})
                        for msg_id, payload in entries
                    ],
                )
            ]
        ),
    )
    return AgentStream(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )


@pytest.mark.anyio
async def test_stream_events_yields_session_event_separately_from_root() -> None:
    child_session_id = uuid.uuid4()
    stream = _stream_with_entries(
        [
            ("1-0", {"type": "text_delta", "part_id": 0, "text": "root"}),
            (
                "2-0",
                {
                    "kind": "session-event",
                    "session_id": str(child_session_id),
                    "event_id": "child-event-1",
                    "event": {"type": "text_delta", "part_id": 0, "text": "child"},
                },
            ),
        ]
    )

    events = [
        event
        async for event in stream._stream_events(
            AsyncMock(side_effect=[False, True]), last_id="0-0"
        )
    ]

    assert len(events) == 2
    root, child = events
    assert isinstance(root, StreamDelta)
    assert root.event.text == "root"
    assert isinstance(child, StreamSessionEvent)
    assert child.id == "2-0"
    assert child.session_id == child_session_id
    assert child.event_id == "child-event-1"
    assert child.event.type is StreamEventType.TEXT_DELTA
    assert child.event.text == "child"


@pytest.mark.anyio
async def test_stream_events_skips_malformed_session_events_and_unknown_kinds() -> None:
    stream = _stream_with_entries(
        [
            ("1-0", {"kind": "some-future-kind", "value": 1}),
            (
                "2-0",
                {
                    "kind": "session-event",
                    "session_id": "not-a-uuid",
                    "event_id": "e1",
                    "event": {"type": "text_delta"},
                },
            ),
            (
                "3-0",
                {
                    "kind": "session-event",
                    "session_id": str(uuid.uuid4()),
                    "event_id": "e2",
                    "event": {"type": "not_a_real_type"},
                },
            ),
            (
                "4-0",
                {
                    "kind": "session-event",
                    "session_id": str(uuid.uuid4()),
                    "event": {"type": "text_delta"},
                },
            ),
            ("5-0", {"type": "text_delta", "part_id": 0, "text": "after"}),
        ]
    )

    events = [
        event
        async for event in stream._stream_events(
            AsyncMock(side_effect=[False, True]), last_id="0-0"
        )
    ]

    # Only the valid root delta survives; nothing surfaces as a stream error.
    assert len(events) == 1
    assert isinstance(events[0], StreamDelta)
    assert events[0].event.text == "after"


@pytest.mark.anyio
async def test_simple_sse_passes_session_event_envelope_through() -> None:
    child_session_id = uuid.uuid4()
    stream = _stream_with_entries(
        [
            (
                "1-0",
                {
                    "kind": "session-event",
                    "session_id": str(child_session_id),
                    "event_id": "child-event-1",
                    "event": {"type": "text_delta", "part_id": 0, "text": "child"},
                },
            ),
        ]
    )

    frames = [
        frame
        async for frame in stream.simple_sse(
            AsyncMock(side_effect=[False, True]), last_id="0-0"
        )
    ]

    session_frames = [f for f in frames if "event: session-event" in f]
    assert len(session_frames) == 1
    lines = session_frames[0].splitlines()
    assert lines[0] == "id: 1-0"
    payload = orjson.loads(lines[2].removeprefix("data: "))
    assert payload["session_id"] == str(child_session_id)
    assert payload["event_id"] == "child-event-1"
    assert payload["event"]["type"] == "text_delta"
    assert payload["event"]["text"] == "child"
    assert not any("event: delta" in f for f in frames)


def test_unified_stream_event_preliminary_wire_round_trip() -> None:
    preliminary = UnifiedStreamEvent.from_dict(
        {
            "type": "tool_result",
            "tool_call_id": "call_subagent",
            "tool_name": "subagent",
            "tool_output": {"session_id": str(uuid.uuid4()), "status": "running"},
            "preliminary": True,
        }
    )
    final = UnifiedStreamEvent.from_dict(
        {
            "type": "tool_result",
            "tool_call_id": "call_subagent",
            "tool_name": "subagent",
            "tool_output": {"session_id": str(uuid.uuid4())},
        }
    )

    assert preliminary.preliminary is True
    assert preliminary.to_dict()["preliminary"] is True
    # Default stays off and is omitted, keeping existing payloads unchanged.
    assert final.preliminary is False
    assert "preliminary" not in final.to_dict()
