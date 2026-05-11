from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from tracecat.agent.adapter.vercel import UIMessage
from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.session.router import get_session, get_session_vercel, send_message
from tracecat.agent.session.types import AgentSessionEntity
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


@pytest.mark.anyio
async def test_get_session_includes_agents_binding() -> None:
    session_stub = _agent_session_stub()
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


@pytest.mark.anyio
async def test_get_session_vercel_includes_agents_binding() -> None:
    session_stub = _agent_session_stub()
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
    fake_stream.sse.assert_called_once()
    assert fake_stream.sse.call_args.kwargs["last_id"] == "0-0"


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
    fake_stream.sse.assert_not_called()
    fake_svc.run_turn.assert_not_awaited()
