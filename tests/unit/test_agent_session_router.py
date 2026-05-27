from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from starlette import status

from tracecat.agent.adapter.vercel import UIMessage
from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.session.router import (
    cancel_session,
    get_session,
    get_session_status,
    get_session_vercel,
    send_message,
    stream_session_events,
)
from tracecat.agent.session.types import AgentSessionEntity, AgentSessionStatus
from tracecat.auth.types import Role
from tracecat.chat.schemas import (
    ApprovalDecision,
    ContinueRunRequest,
    VercelChatRequest,
)
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError


async def _empty_event_stream() -> AsyncIterator[None]:
    if False:
        yield


def _agent_session_stub(**overrides: Any) -> SimpleNamespace:
    now = overrides.pop("now", None)
    if now is None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)

    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "title": "New Chat",
        "created_by": uuid.uuid4(),
        "entity_type": AgentSessionEntity.AGENT_PRESET,
        "entity_id": uuid.uuid4(),
        "channel_context": None,
        "tools": None,
        "agent_preset_id": uuid.uuid4(),
        "agent_preset_version_id": uuid.uuid4(),
        "agents_binding": {"enabled": False},
        "harness_type": HarnessType.CLAUDE_CODE,
        "created_at": now,
        "updated_at": now,
        "last_stream_id": None,
        "status": "idle",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _read_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read"}),
    )


class _AsyncContext:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


@pytest.mark.anyio
async def test_get_session_includes_agents_binding() -> None:
    session_stub = _agent_session_stub(status=AgentSessionStatus.RUNNING.value)
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=session_stub),
        list_messages=AsyncMock(return_value=[]),
    )

    with patch(
        "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
    ):
        raw_get_session = cast(Any, get_session).__wrapped__
        response = await raw_get_session(
            session_id=session_stub.id,
            role=_read_role(session_stub.workspace_id),
            session=AsyncMock(),
        )

    assert response.model_dump(mode="json")["agents_binding"] == {
        "enabled": False,
        "subagents": [],
    }
    assert response.turn_status is AgentSessionStatus.RUNNING


@pytest.mark.anyio
async def test_get_session_vercel_includes_agents_binding() -> None:
    session_stub = _agent_session_stub(
        status=AgentSessionStatus.WAITING_FOR_APPROVAL.value
    )
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(return_value=session_stub),
        list_messages=AsyncMock(return_value=[]),
    )

    with patch(
        "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
    ):
        raw_get_session_vercel = cast(Any, get_session_vercel).__wrapped__
        response = await raw_get_session_vercel(
            session_id=session_stub.id,
            role=_read_role(session_stub.workspace_id),
            session=AsyncMock(),
        )

    assert response.model_dump(mode="json")["agents_binding"] == {
        "enabled": False,
        "subagents": [],
    }
    assert response.turn_status is AgentSessionStatus.WAITING_FOR_APPROVAL


@pytest.mark.anyio
async def test_cancel_session_delegates_to_service() -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = _read_role(workspace_id)
    fake_response = SimpleNamespace(
        session_id=session_id,
        run_id=uuid.uuid4(),
        reason="user_cancel",
        status="running",
    )
    fake_svc = SimpleNamespace(request_cancel=AsyncMock(return_value=fake_response))

    with patch(
        "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
    ):
        raw_cancel_session = cast(Any, cancel_session).__wrapped__
        response = await raw_cancel_session(
            session_id=session_id,
            role=role,
            session=AsyncMock(),
            request=None,
        )

    assert response is fake_response
    fake_svc.request_cancel.assert_awaited_once()
    assert fake_svc.request_cancel.await_args.args[0] == session_id
    assert fake_svc.request_cancel.await_args.args[1].reason == "user_cancel"


@pytest.mark.anyio
async def test_cancel_session_maps_conflict_to_409() -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = _read_role(workspace_id)
    fake_svc = SimpleNamespace(
        request_cancel=AsyncMock(
            side_effect=TracecatConflictError(
                "not running",
                detail={"status": "idle"},
            )
        )
    )

    with patch(
        "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
    ):
        raw_cancel_session = cast(Any, cancel_session).__wrapped__
        with pytest.raises(HTTPException) as exc_info:
            await raw_cancel_session(
                session_id=session_id,
                role=role,
                session=AsyncMock(),
                request=None,
            )

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.detail == {"status": "idle"}


@pytest.mark.anyio
@pytest.mark.parametrize("status_value", ["running", "waiting_for_approval"])
async def test_get_session_status_includes_prompt_for_active_turn(
    status_value: str,
) -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    run_id = uuid.uuid4()
    fake_svc = SimpleNamespace(
        get_session=AsyncMock(
            return_value=SimpleNamespace(
                status=status_value,
                curr_run_id=run_id,
            )
        ),
        get_active_run_prompt=AsyncMock(return_value="Investigate this alert"),
    )

    with patch(
        "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
    ):
        raw_get_session_status = cast(Any, get_session_status).__wrapped__
        response = await raw_get_session_status(
            session_id=session_id,
            role=_read_role(workspace_id),
            session=AsyncMock(),
        )

    assert response.turn_status == status_value
    assert response.curr_run_id == run_id
    assert response.prompt == "Investigate this alert"
    fake_svc.get_active_run_prompt.assert_awaited_once_with(session_id, run_id)


@pytest.mark.anyio
async def test_send_message_continue_uses_path_session_id_for_stream_key() -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    request = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )

    fake_svc = SimpleNamespace(
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(return_value=None),
        run_turn=AsyncMock(return_value=None),
        get_session=AsyncMock(return_value=SimpleNamespace(curr_run_id=uuid.uuid4())),
    )
    fake_stream = SimpleNamespace(
        reset_for_new_turn=AsyncMock(return_value=None),
        abort_new_turn=AsyncMock(return_value=None),
        sse=Mock(return_value=_empty_event_stream()),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ) as with_session_mock,
        patch(
            "tracecat.agent.session.router.AgentStream.new",
            AsyncMock(return_value=fake_stream),
        ) as stream_new_mock,
    ):
        raw_send_message = cast(Any, send_message).__wrapped__
        response = await raw_send_message(
            session_id=session_id,
            request=request,
            role=role,
            http_request=cast(
                Any,
                SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
            ),
        )

    assert isinstance(response, StreamingResponse)
    with_session_mock.assert_called_once_with(role=role)
    stream_new_mock.assert_awaited_once_with(session_id, workspace_id)
    fake_svc.validate_turn_request.assert_awaited_once_with(
        session_id=session_id,
        request=request,
    )
    fake_stream.reset_for_new_turn.assert_not_awaited()
    fake_stream.sse.assert_called_once()
    assert fake_stream.sse.call_args.kwargs["last_id"] == "$"
    fake_svc.run_turn.assert_awaited_once_with(
        session_id=session_id,
        request=request,
    )


@pytest.mark.anyio
async def test_send_message_new_turn_resets_stream_before_streaming() -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    request = VercelChatRequest(
        message=UIMessage(
            id="msg-1",
            role="user",
            parts=[{"type": "text", "text": "hello"}],
        ),
        model="gpt-4o-mini",
        model_provider="openai",
    )

    run_id = uuid.uuid4()
    fake_svc = SimpleNamespace(
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(return_value=None),
        run_turn=AsyncMock(return_value=None),
        get_session=AsyncMock(return_value=SimpleNamespace(curr_run_id=run_id)),
    )
    fake_stream = SimpleNamespace(
        reset_for_new_turn=AsyncMock(return_value=None),
        append=AsyncMock(return_value=None),
        abort_new_turn=AsyncMock(return_value=None),
        sse=Mock(return_value=_empty_event_stream()),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ) as with_session_mock,
        patch(
            "tracecat.agent.session.router.AgentStream.new",
            AsyncMock(return_value=fake_stream),
        ),
    ):
        raw_send_message = cast(Any, send_message).__wrapped__
        response = await raw_send_message(
            session_id=session_id,
            request=request,
            role=role,
            http_request=cast(
                Any,
                SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
            ),
        )

    assert isinstance(response, StreamingResponse)
    with_session_mock.assert_called_once_with(role=role)
    fake_stream.reset_for_new_turn.assert_awaited_once()
    fake_stream.abort_new_turn.assert_not_awaited()
    fake_stream.sse.assert_called_once()
    assert fake_stream.sse.call_args.kwargs["last_id"] == "0-0"


@pytest.mark.anyio
async def test_send_message_new_turn_clears_stream_when_startup_fails() -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    request = VercelChatRequest(
        message=UIMessage(
            id="msg-1",
            role="user",
            parts=[{"type": "text", "text": "hello"}],
        ),
        model="gpt-4o-mini",
        model_provider="openai",
    )

    fake_svc = SimpleNamespace(
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(return_value=None),
        run_turn=AsyncMock(side_effect=RuntimeError("temporal unavailable")),
    )
    fake_stream = SimpleNamespace(
        reset_for_new_turn=AsyncMock(return_value=None),
        abort_new_turn=AsyncMock(return_value=None),
        sse=Mock(return_value=_empty_event_stream()),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ),
        patch(
            "tracecat.agent.session.router.AgentStream.new",
            AsyncMock(return_value=fake_stream),
        ),
    ):
        raw_send_message = cast(Any, send_message).__wrapped__
        with pytest.raises(HTTPException) as exc_info:
            await raw_send_message(
                session_id=session_id,
                request=request,
                role=role,
                http_request=cast(
                    Any,
                    SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
                ),
            )

    assert exc_info.value.status_code == 500
    fake_stream.reset_for_new_turn.assert_awaited_once()
    fake_svc.run_turn.assert_awaited_once_with(
        session_id=session_id,
        request=request,
    )
    fake_stream.abort_new_turn.assert_awaited_once()
    fake_stream.sse.assert_not_called()


@pytest.mark.anyio
async def test_send_message_does_not_reset_stream_when_validation_fails() -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    request = ContinueRunRequest(
        decisions=[
            ApprovalDecision(
                tool_call_id="tool_call_123",
                action="approve",
            )
        ],
        source="inbox",
    )

    fake_svc = SimpleNamespace(
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(
            side_effect=TracecatNotFoundError("No active workflow run")
        ),
        run_turn=AsyncMock(return_value=None),
    )
    fake_stream = SimpleNamespace(
        reset_for_new_turn=AsyncMock(return_value=None),
        abort_new_turn=AsyncMock(return_value=None),
        sse=Mock(return_value=_empty_event_stream()),
    )

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ) as with_session_mock,
        patch(
            "tracecat.agent.session.router.AgentStream.new",
            AsyncMock(return_value=fake_stream),
        ) as stream_new_mock,
    ):
        raw_send_message = cast(Any, send_message).__wrapped__
        with pytest.raises(HTTPException, match="No active workflow run") as exc_info:
            await raw_send_message(
                session_id=session_id,
                request=request,
                role=role,
                http_request=cast(
                    Any,
                    SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
                ),
            )

    assert exc_info.value.status_code == 404
    with_session_mock.assert_called_once_with(role=role)
    stream_new_mock.assert_awaited_once_with(session_id, workspace_id)
    fake_stream.reset_for_new_turn.assert_not_awaited()
    fake_stream.abort_new_turn.assert_not_awaited()
    fake_stream.sse.assert_not_called()
    fake_svc.run_turn.assert_not_awaited()


def _make_stream_role(workspace_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read"}),
    )


def _fake_stream_session(
    *,
    status_value: str,
    last_stream_id: str | None = None,
    curr_run_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status_value,
        last_stream_id=last_stream_id,
        curr_run_id=curr_run_id,
    )


def _fake_stream(min_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        sse=Mock(return_value=_empty_event_stream()),
        finished_sse=Mock(return_value=_empty_event_stream()),
        min_entry_id=AsyncMock(return_value=min_id),
    )


async def _run_stream_endpoint(
    *,
    session: SimpleNamespace | None,
    stream: SimpleNamespace,
    headers: dict[str, str],
) -> Any:
    role = _make_stream_role(uuid.uuid4())
    fake_svc = SimpleNamespace(get_session=AsyncMock(return_value=session))
    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService.with_session",
            return_value=_AsyncContext(fake_svc),
        ),
        patch(
            "tracecat.agent.session.router.AgentStream.new",
            AsyncMock(return_value=stream),
        ),
    ):
        raw = cast(Any, stream_session_events).__wrapped__
        return await raw(
            role=role,
            request=SimpleNamespace(
                headers=headers, is_disconnected=AsyncMock(return_value=False)
            ),
            session_id=uuid.uuid4(),
        )


@pytest.mark.anyio
async def test_stream_session_events_returns_204_when_no_turn_started() -> None:
    """Idle status with no Last-Event-ID header returns 204."""
    stream = _fake_stream()
    response = await _run_stream_endpoint(
        session=_fake_stream_session(status_value="idle"),
        stream=stream,
        headers={},
    )

    assert isinstance(response, Response)
    assert response.status_code == status.HTTP_204_NO_CONTENT
    stream.sse.assert_not_called()


@pytest.mark.anyio
async def test_stream_session_events_attaches_when_turn_in_progress_no_events_yet() -> (
    None
):
    stream = _fake_stream()
    response = await _run_stream_endpoint(
        session=_fake_stream_session(status_value="running"),
        stream=stream,
        headers={},
    )

    assert isinstance(response, StreamingResponse)
    stream.sse.assert_called_once()
    assert stream.sse.call_args.kwargs["last_id"] == "0-0"


@pytest.mark.anyio
async def test_stream_session_events_resumes_after_last_event_id() -> None:
    """A live cursor newer than the buffer min resumes after it (composite id)."""
    stream = _fake_stream(min_id="1000-0")
    response = await _run_stream_endpoint(
        session=_fake_stream_session(status_value="running"),
        stream=stream,
        headers={"Last-Event-ID": "1234-0:2"},
    )

    assert isinstance(response, StreamingResponse)
    stream.sse.assert_called_once()
    assert stream.sse.call_args.kwargs["last_id"] == "1234-0"


@pytest.mark.anyio
async def test_stream_session_events_stale_cursor_running_replays_from_start() -> None:
    """Cursor older than the buffer min, still running -> replay from 0-0."""
    stream = _fake_stream(min_id="2000-0")
    response = await _run_stream_endpoint(
        session=_fake_stream_session(status_value="running"),
        stream=stream,
        headers={"Last-Event-ID": "1000-0:0"},
    )

    assert isinstance(response, StreamingResponse)
    stream.sse.assert_called_once()
    assert stream.sse.call_args.kwargs["last_id"] == "0-0"
    stream.finished_sse.assert_not_called()


@pytest.mark.anyio
async def test_stream_session_events_stale_cursor_terminal_finishes() -> None:
    """Cursor older than the buffer min and turn terminal -> finishing stream."""
    stream = _fake_stream(min_id=None)
    response = await _run_stream_endpoint(
        session=_fake_stream_session(status_value="stopped"),
        stream=stream,
        headers={"Last-Event-ID": "1000-0:0"},
    )

    assert isinstance(response, StreamingResponse)
    stream.finished_sse.assert_called_once()
    stream.sse.assert_not_called()
