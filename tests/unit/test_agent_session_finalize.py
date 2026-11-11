from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.stream.feed import SessionFeedEventType
from tracecat.auth.types import Role


def _build_service(
    call_order: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AgentSessionService, SimpleNamespace, Role]:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )

    async def scalar(_: object) -> uuid.UUID:
        call_order.append("update")
        return uuid.uuid4()

    async def commit() -> None:
        call_order.append("commit")

    async def publish(**kwargs: Any) -> None:
        assert kwargs["type"] == SessionFeedEventType.RUN_FINISHED
        call_order.append("publish")

    monkeypatch.setattr("tracecat.agent.session.service.publish_session_event", publish)
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=scalar),
        commit=AsyncMock(side_effect=commit),
    )
    return AgentSessionService(cast(Any, session), role), session, role


@pytest.mark.anyio
async def test_finalize_turn_commits_pointers_before_emitting_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_order: list[str] = []
    service, _, role = _build_service(call_order, monkeypatch)
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    active_stream_id = uuid.uuid4()
    expected_session_id = session_id

    async def done() -> None:
        call_order.append("done")

    stream = SimpleNamespace(done=done)

    async def open_stream(
        *,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID,
        stream_id: uuid.UUID | None,
    ) -> SimpleNamespace:
        assert workspace_id == role.workspace_id
        assert session_id == expected_session_id
        assert stream_id == active_stream_id
        call_order.append("open_stream")
        return stream

    monkeypatch.setattr(
        "tracecat.agent.session.service.AgentStream.new",
        open_stream,
    )

    await service.finalize_turn(
        session_id,
        run_id,
        active_stream_id=active_stream_id,
    )

    assert call_order == ["update", "commit", "publish", "open_stream", "done"]


@pytest.mark.anyio
async def test_finalize_turn_propagates_stream_failure_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_order: list[str] = []
    service, session, _ = _build_service(call_order, monkeypatch)

    async def done() -> None:
        call_order.append("done")
        raise RuntimeError("redis unavailable")

    async def open_stream(**_: object) -> SimpleNamespace:
        call_order.append("open_stream")
        return SimpleNamespace(done=done)

    monkeypatch.setattr(
        "tracecat.agent.session.service.AgentStream.new",
        open_stream,
    )

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await service.finalize_turn(
            uuid.uuid4(),
            uuid.uuid4(),
            active_stream_id=uuid.uuid4(),
        )

    session.commit.assert_awaited_once()
    assert call_order == ["update", "commit", "publish", "open_stream", "done"]
