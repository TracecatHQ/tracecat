from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.agent.session.activities import (
    CreateSessionInput,
    create_session_activity,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role


@pytest.mark.anyio
@patch("tracecat.agent.session.activities.AgentSessionService.with_session")
async def test_create_session_activity_requests_fork(
    mock_with_session: MagicMock,
) -> None:
    mock_role = Role(
        type="service",
        service_id="tracecat-agent-executor",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
    )
    mock_session_id = uuid.uuid4()
    parent_session_id = uuid.uuid4()
    input = CreateSessionInput(
        role=mock_role,
        session_id=mock_session_id,
        parent_session_id=parent_session_id,
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )
    mock_service = AsyncMock()
    mock_service.get_or_create_session.return_value = (MagicMock(), True)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_service
    mock_with_session.return_value = mock_ctx

    stream = AsyncMock()
    with patch(
        "tracecat.agent.session.activities.AgentStream.new",
        AsyncMock(return_value=stream),
    ):
        result = await create_session_activity(input)

    assert result.success is True
    stream.clear_buffer.assert_awaited_once_with()
    mock_service.get_or_create_session.assert_awaited_once()
    assert (
        mock_service.get_or_create_session.call_args.kwargs["parent_session_id"]
        == parent_session_id
    )
