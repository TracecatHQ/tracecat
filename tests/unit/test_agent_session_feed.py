from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import orjson
import pytest
from asyncpg.pgproto.pgproto import UUID as PgUUID
from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import Future, RetryError

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.session.router import stream_session_feed
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.stream.feed import (
    FEED_MAX_CONNECTION_SECONDS,
    FEED_MAXLEN,
    FEED_TTL_SECONDS,
    SessionFeed,
    SessionFeedEvent,
    SessionFeedEventType,
    SessionFeedSnapshot,
    publish_session_event,
)
from tracecat.auth.types import Role
from tracecat.chat import tokens
from tracecat.db.models import AgentSession, Approval
from tracecat.redis.client import RedisClient


def _feed(
    client: object, workspace_id: uuid.UUID, session_id: uuid.UUID
) -> SessionFeed:
    return SessionFeed(
        client=cast(RedisClient, client),
        workspace_id=workspace_id,
        session_id=session_id,
    )


def _entry(entry_id: str, payload: object) -> tuple[str, dict[str, str]]:
    return entry_id, {tokens.DATA_KEY: orjson.dumps(payload).decode()}


def _stop_after(calls: int) -> AsyncMock:
    return AsyncMock(side_effect=[False] * calls + [True])


@pytest.mark.anyio
async def test_publish_writes_state_only_entry_with_cap_and_ttl() -> None:
    workspace_id, session_id = uuid.uuid4(), uuid.uuid4()
    # Publish sites pass row values, which asyncpg returns as its own UUID type.
    run_id, stream_id = PgUUID(str(uuid.uuid4())), PgUUID(str(uuid.uuid4()))
    client = SimpleNamespace(xadd=AsyncMock(return_value="1-0"))

    await _feed(client, workspace_id, session_id).publish(
        SessionFeedEvent(
            type=SessionFeedEventType.RUN_RESUMED, run_id=run_id, stream_id=stream_id
        )
    )

    client.xadd.assert_awaited_once()
    assert client.xadd.await_args is not None
    key, fields = client.xadd.await_args.args
    assert key == f"agent-feed:{workspace_id}:{session_id}"
    assert orjson.loads(fields[tokens.DATA_KEY]) == {
        "type": "run.resumed",
        "run_id": str(run_id),
        "stream_id": str(stream_id),
    }
    assert client.xadd.await_args.kwargs == {
        "maxlen": FEED_MAXLEN,
        "approximate": True,
        "expire_seconds": FEED_TTL_SECONDS,
    }


@pytest.mark.anyio
async def test_latest_id_defaults_to_start_of_an_empty_feed() -> None:
    client = SimpleNamespace(xrevrange=AsyncMock(return_value=[]))
    feed = _feed(client, uuid.uuid4(), uuid.uuid4())
    assert await feed.latest_id() == "0-0"

    client.xrevrange.return_value = [_entry("7-0", {})]
    assert await feed.latest_id() == "7-0"
    assert client.xrevrange.await_args is not None
    assert client.xrevrange.await_args.kwargs == {"count": 1}


def _frame_data(frame: str) -> object:
    data = next(line for line in frame.splitlines() if line.startswith("data: "))
    return orjson.loads(data.removeprefix("data: "))


async def _frames(feed: SessionFeed, after_id: str, reads: int) -> list[str]:
    snapshot = SessionFeedSnapshot(run_id=None, stream_id=None, paused=False)
    with patch("tracecat.agent.stream.feed.asyncio.sleep", AsyncMock()):
        return [
            frame async for frame in feed.sse(snapshot, after_id, _stop_after(reads))
        ]


@pytest.mark.anyio
async def test_sse_sends_snapshot_then_entries_in_order() -> None:
    run_id, stream_id = uuid.uuid4(), uuid.uuid4()
    workspace_id, session_id = uuid.uuid4(), uuid.uuid4()
    key = f"agent-feed:{workspace_id}:{session_id}"
    started = {"type": "run.started", "run_id": str(run_id), "stream_id": None}
    finished = {"type": "run.finished", "run_id": str(run_id), "stream_id": None}
    client = SimpleNamespace(
        xread=AsyncMock(
            side_effect=[
                [(key, [_entry("2-0", started), ("3-0", {"unexpected": "field"})])],
                [(key, [_entry("4-0", finished)])],
            ]
        )
    )
    snapshot = SessionFeedSnapshot(
        run_id=PgUUID(str(run_id)), stream_id=PgUUID(str(stream_id)), paused=True
    )
    feed = _feed(client, workspace_id, session_id)

    with patch("tracecat.agent.stream.feed.asyncio.sleep", AsyncMock()):
        frames = [frame async for frame in feed.sse(snapshot, "1-0", _stop_after(2))]

    # The client reconnects quickly after the server closes an aged-out feed.
    assert frames[0].startswith("retry: ")
    assert [_frame_data(frame) for frame in frames] == [
        {
            "type": "snapshot",
            "run_id": str(run_id),
            "stream_id": str(stream_id),
            "paused": True,
            "feed_id": "1-0",
        },
        started,
        finished,
    ]
    # Entry ids let a reconnecting client tell whether it missed anything.
    assert [frame.splitlines()[0] for frame in frames[1:]] == ["id: 2-0", "id: 4-0"]
    # The cursor advances past skipped entries, and reads never block.
    calls = [call.kwargs for call in client.xread.await_args_list]
    assert calls == [
        {"streams": {key: "1-0"}, "count": 100},
        {"streams": {key: "3-0"}, "count": 100},
    ]


@pytest.mark.anyio
async def test_sse_keeps_reading_after_a_redis_error() -> None:
    run_id = uuid.uuid4()
    workspace_id, session_id = uuid.uuid4(), uuid.uuid4()
    key = f"agent-feed:{workspace_id}:{session_id}"
    paused = {"type": "run.paused", "run_id": str(run_id), "stream_id": None}
    client = SimpleNamespace(
        xread=AsyncMock(
            side_effect=[
                ConnectionError("redis blip"),
                [(key, [_entry("2-0", paused)])],
            ]
        )
    )

    frames = await _frames(_feed(client, workspace_id, session_id), "0-0", reads=2)

    assert [_frame_data(frame) for frame in frames[1:]] == [paused]


@pytest.mark.anyio
async def test_sse_closes_an_aged_out_connection() -> None:
    client = SimpleNamespace(xread=AsyncMock(return_value=[]))
    clock = iter([0.0, FEED_MAX_CONNECTION_SECONDS])

    with patch("tracecat.agent.stream.feed.monotonic", lambda: next(clock)):
        frames = await _frames(_feed(client, uuid.uuid4(), uuid.uuid4()), "0-0", 5)

    assert len(frames) == 1
    client.xread.assert_not_awaited()


@pytest.mark.anyio
async def test_publish_session_event_is_best_effort() -> None:
    run_id = uuid.uuid4()
    with patch(
        "tracecat.agent.stream.feed.SessionFeed.new",
        AsyncMock(side_effect=ConnectionError("redis down")),
    ) as new:
        await publish_session_event(
            workspace_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            type=SessionFeedEventType.RUN_STARTED,
            run_id=run_id,
        )
        new.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        RedisConnectionError("redis down"),
        # The client's retry decorator wraps the final Redis error.
        RetryError(Future(attempt_number=3)),
    ],
)
async def test_feed_route_reports_redis_outage_as_unavailable(
    error: Exception,
) -> None:
    with (
        patch(
            "tracecat.agent.session.router.SessionFeed.new",
            AsyncMock(side_effect=error),
        ),
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session"
        ) as with_session,
        pytest.raises(HTTPException) as caught,
    ):
        raw = cast(Any, stream_session_feed).__wrapped__
        await raw(
            role=_route_role(uuid.uuid4()),
            request=SimpleNamespace(is_disconnected=AsyncMock()),
            session_id=uuid.uuid4(),
        )
    assert caught.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    with_session.assert_not_called()


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *_: object) -> None:
        return None


def _route_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="user",
        user_id=uuid.uuid4(),
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"agent:read"}),
    )


@pytest.mark.anyio
async def test_feed_route_reads_feed_position_before_the_row() -> None:
    workspace_id, session_id = uuid.uuid4(), uuid.uuid4()
    order: list[str] = []
    snapshot = SessionFeedSnapshot(run_id=None, stream_id=None, paused=False)

    async def latest_id() -> str:
        order.append("feed_position")
        return "41-0"

    async def get_feed_snapshot(requested: uuid.UUID) -> SessionFeedSnapshot:
        assert requested == session_id
        order.append("row")
        return snapshot

    feed = SimpleNamespace(latest_id=latest_id, sse=AsyncMock())
    svc = SimpleNamespace(get_feed_snapshot=get_feed_snapshot)
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))

    with (
        patch(
            "tracecat.agent.session.router.SessionFeed.new",
            AsyncMock(return_value=feed),
        ) as feed_new,
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(svc),
        ),
    ):
        raw = cast(Any, stream_session_feed).__wrapped__
        response = await raw(
            role=_route_role(workspace_id), request=request, session_id=session_id
        )

    assert isinstance(response, StreamingResponse)
    assert order == ["feed_position", "row"]
    feed_new.assert_awaited_once_with(workspace_id=workspace_id, session_id=session_id)
    feed.sse.assert_called_once_with(snapshot, "41-0", request.is_disconnected)


@pytest.mark.anyio
async def test_feed_route_rejects_an_unknown_session() -> None:
    feed = SimpleNamespace(latest_id=AsyncMock(return_value="0-0"))
    svc = SimpleNamespace(get_feed_snapshot=AsyncMock(return_value=None))
    with (
        patch(
            "tracecat.agent.session.router.SessionFeed.new",
            AsyncMock(return_value=feed),
        ),
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(svc),
        ),
        pytest.raises(HTTPException) as caught,
    ):
        raw = cast(Any, stream_session_feed).__wrapped__
        await raw(
            role=_route_role(uuid.uuid4()),
            request=SimpleNamespace(is_disconnected=AsyncMock()),
            session_id=uuid.uuid4(),
        )
    assert caught.value.status_code == status.HTTP_404_NOT_FOUND


async def _seed_session(
    session: AsyncSession,
    role: Role,
    *,
    run_id: uuid.UUID | None = None,
    stream_id: uuid.UUID | None = None,
) -> AgentSession:
    agent_session = AgentSession(
        id=uuid.uuid4(),
        title="Feed",
        workspace_id=role.workspace_id,
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=uuid.uuid4(),
        curr_run_id=run_id,
        active_stream_id=stream_id,
    )
    session.add(agent_session)
    await session.commit()
    return agent_session


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_feed_snapshot_reflects_the_session_row(
    session: AsyncSession, svc_role: Role
) -> None:
    service = AgentSessionService(session=session, role=svc_role)
    idle = await _seed_session(session, svc_role)
    run_id, stream_id = uuid.uuid4(), uuid.uuid4()
    paused = await _seed_session(session, svc_role, run_id=run_id, stream_id=stream_id)
    session.add(
        Approval(
            workspace_id=svc_role.workspace_id,
            session_id=paused.id,
            tool_call_id="tool_call_1",
            tool_name="core.http_request",
            status=ApprovalStatus.PENDING,
        )
    )
    await session.commit()

    assert await service.get_feed_snapshot(idle.id) == SessionFeedSnapshot(
        run_id=None, stream_id=None, paused=False
    )
    assert await service.get_feed_snapshot(paused.id) == SessionFeedSnapshot(
        run_id=run_id, stream_id=stream_id, paused=True
    )
    assert await service.get_feed_snapshot(uuid.uuid4()) is None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_pointer_cleanup_publishes_finished_only_when_it_releases(
    session: AsyncSession, svc_role: Role
) -> None:
    service = AgentSessionService(session=session, role=svc_role)
    run_id, stream_id = uuid.uuid4(), uuid.uuid4()
    startup_failed = await _seed_session(
        session, svc_role, run_id=run_id, stream_id=stream_id
    )
    finished_run_id = uuid.uuid4()
    finished = await _seed_session(
        session, svc_role, run_id=finished_run_id, stream_id=uuid.uuid4()
    )

    with patch(
        "tracecat.agent.session.service.publish_session_event", AsyncMock()
    ) as publish:
        # Stale cleanups match nothing and announce nothing.
        await service.clear_active_turn(
            startup_failed.id, expected_stream_id=uuid.uuid4()
        )
        await service.clear_turn_pointers(finished.id, uuid.uuid4())
        publish.assert_not_awaited()

        await service.clear_active_turn(startup_failed.id, expected_stream_id=stream_id)
        await service.clear_turn_pointers(finished.id, finished_run_id)

    assert [call.kwargs for call in publish.await_args_list] == [
        {
            "workspace_id": svc_role.workspace_id,
            "session_id": startup_failed.id,
            "type": SessionFeedEventType.RUN_FINISHED,
            "run_id": run_id,
        },
        {
            "workspace_id": svc_role.workspace_id,
            "session_id": finished.id,
            "type": SessionFeedEventType.RUN_FINISHED,
            "run_id": finished_run_id,
        },
    ]
