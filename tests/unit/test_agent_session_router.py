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
from tracecat.agent.session.router import send_message, stream_session_events
from tracecat.auth.types import Role
from tracecat.chat.schemas import (
    ApprovalDecision,
    ContinueRunRequest,
    VercelChatRequest,
)
from tracecat.exceptions import TracecatNotFoundError


async def _empty_event_stream() -> AsyncIterator[None]:
    if False:
        yield


class _AsyncContext:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


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

    fake_svc = SimpleNamespace(
        is_legacy_session=AsyncMock(return_value=False),
        validate_turn_request=AsyncMock(return_value=None),
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


@pytest.mark.anyio
async def test_stream_session_events_returns_204_when_no_turn_started() -> None:
    """last_stream_id=None with no Last-Event-ID header → 204, no stream attached."""
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = _make_stream_role(workspace_id)

    fake_session = SimpleNamespace(last_stream_id=None)
    fake_svc = SimpleNamespace(get_session=AsyncMock(return_value=fake_session))
    fake_stream = SimpleNamespace(sse=Mock(return_value=_empty_event_stream()))

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
        raw = cast(Any, stream_session_events).__wrapped__
        response = await raw(
            role=role,
            request=SimpleNamespace(headers={}),
            session_id=session_id,
        )

    assert isinstance(response, Response)
    assert response.status_code == status.HTTP_204_NO_CONTENT
    fake_stream.sse.assert_not_called()


@pytest.mark.anyio
async def test_stream_session_events_attaches_when_turn_in_progress_no_events_yet() -> (
    None
):
    """last_stream_id="0-0" (reset_for_new_turn written, key not yet in Redis) → stream, no 204.

    This is the regression case: the stream key doesn't exist yet but a turn is
    active. The old Redis-exists check would have incorrectly returned 204 here.
    """
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = _make_stream_role(workspace_id)

    fake_session = SimpleNamespace(last_stream_id="0-0")
    fake_svc = SimpleNamespace(get_session=AsyncMock(return_value=fake_session))
    fake_stream = SimpleNamespace(sse=Mock(return_value=_empty_event_stream()))

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
        raw = cast(Any, stream_session_events).__wrapped__
        response = await raw(
            role=role,
            request=SimpleNamespace(
                headers={}, is_disconnected=AsyncMock(return_value=False)
            ),
            session_id=session_id,
        )

    assert isinstance(response, StreamingResponse)
    fake_stream.sse.assert_called_once()


@pytest.mark.anyio
async def test_stream_session_events_attaches_when_last_event_id_present() -> None:
    """Last-Event-ID header present → always attach regardless of last_stream_id."""
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    role = _make_stream_role(workspace_id)

    fake_session = SimpleNamespace(last_stream_id=None)
    fake_svc = SimpleNamespace(get_session=AsyncMock(return_value=fake_session))
    fake_stream = SimpleNamespace(sse=Mock(return_value=_empty_event_stream()))

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
        raw = cast(Any, stream_session_events).__wrapped__
        response = await raw(
            role=role,
            request=SimpleNamespace(
                headers={"Last-Event-ID": "1234-0"},
                is_disconnected=AsyncMock(return_value=False),
            ),
            session_id=session_id,
        )

    assert isinstance(response, StreamingResponse)
    fake_stream.sse.assert_called_once()
