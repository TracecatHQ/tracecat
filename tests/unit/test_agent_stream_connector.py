from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamDelta, StreamEnd
from tracecat.auth.types import Role
from tracecat.chat import tokens
from tracecat.redis.client import RedisClient


@pytest.mark.anyio
async def test_stream_events_clears_buffer_after_terminal_marker() -> None:
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

    stream._set_last_stream_id = AsyncMock()

    events = [
        event
        async for event in stream._stream_events(
            AsyncMock(side_effect=[False, True]), last_id="0-0"
        )
    ]
    event = events[0]

    assert isinstance(event, StreamEnd)

    stream._set_last_stream_id.assert_awaited_once_with(None)
    raw_client.expire.assert_awaited_once_with(
        name=stream._stream_key,
        time=stream.COMPLETED_STREAM_TTL_SECONDS,
    )


@pytest.mark.anyio
async def test_stream_events_preserves_cursor_when_stream_not_completed() -> None:
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
    stream._set_last_stream_id = AsyncMock()

    events = [
        event async for event in stream._stream_events(stop_condition, last_id="0-0")
    ]

    assert len(events) == 1
    assert isinstance(events[0], StreamDelta)
    stream._set_last_stream_id.assert_awaited()
    raw_client.expire.assert_not_awaited()


@pytest.mark.anyio
async def test_set_last_stream_id_bootstraps_workspace_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    session_id = uuid.uuid4()
    stream = AgentStream(
        client=cast(RedisClient, SimpleNamespace()),
        workspace_id=workspace_id,
        session_id=session_id,
    )
    role = Role(
        type="service",
        user_id=None,
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"*"}),
    )
    fake_session_service = SimpleNamespace(
        get_session=AsyncMock(return_value=SimpleNamespace(id=session_id)),
        update_last_stream_id=AsyncMock(return_value=None),
    )
    with_session = Mock()

    @asynccontextmanager
    async def fake_with_session(*, role: Role | None = None, session=None):
        with_session(role=role, session=session)
        yield fake_session_service

    monkeypatch.setattr(
        stream, "_build_session_service_role", AsyncMock(return_value=role)
    )
    monkeypatch.setattr(AgentSessionService, "with_session", fake_with_session)

    await stream._set_last_stream_id("1717426372768-0")

    with_session.assert_called_once()
    assert with_session.call_args.kwargs["role"] == role
    fake_session_service.update_last_stream_id.assert_awaited_once()
