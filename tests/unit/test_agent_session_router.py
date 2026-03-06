from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.responses import StreamingResponse

from tracecat.agent.session.router import send_message
from tracecat.auth.types import Role
from tracecat.chat.schemas import ApprovalDecision, ContinueRunRequest


class _SessionSentinel:
    last_stream_id: str | None = None

    @property
    def id(self) -> uuid.UUID:
        raise AssertionError("send_message should not access ORM session.id here")


async def _empty_event_stream() -> AsyncIterator[None]:
    if False:
        yield


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
        get_session=AsyncMock(return_value=_SessionSentinel()),
        run_turn=AsyncMock(return_value=None),
    )
    fake_stream = SimpleNamespace(sse=Mock(return_value=_empty_event_stream()))

    with (
        patch(
            "tracecat.agent.session.router.AgentSessionService", return_value=fake_svc
        ),
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
            session=AsyncMock(),
            http_request=cast(
                Any,
                SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
            ),
        )

    assert isinstance(response, StreamingResponse)
    stream_new_mock.assert_awaited_once_with(session_id, workspace_id)
