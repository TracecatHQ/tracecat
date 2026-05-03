from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.agent.session.schemas import AgentSessionRead
from tracecat.agent.session.service import AgentSessionService
from tracecat.auth.types import Role


def _mock_scalar_result(items: list[Any]) -> Mock:
    scalars = Mock()
    scalars.all.return_value = items
    result = Mock()
    result.scalars.return_value = scalars
    return result


def _build_service() -> tuple[AgentSessionService, SimpleNamespace, Role]:
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        user_id=uuid.uuid4(),
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read"}),
    )
    session = SimpleNamespace(execute=AsyncMock())
    return AgentSessionService(cast(Any, session), role), session, role


def _agent_session_row(
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    parent_session_id: uuid.UUID | None,
) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        title="Child approval chat",
        created_by=user_id,
        entity_type="approval",
        entity_id=uuid.uuid4(),
        channel_context=None,
        tools=None,
        agent_preset_id=None,
        agent_preset_version_id=None,
        harness_type=None,
        last_stream_id=None,
        parent_session_id=parent_session_id,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.anyio
async def test_list_sessions_parent_session_filter_excludes_legacy_chats() -> None:
    service, session, role = _build_service()
    assert role.workspace_id is not None
    assert role.user_id is not None
    parent_session_id = uuid.uuid4()
    child_session = _agent_session_row(
        workspace_id=role.workspace_id,
        user_id=role.user_id,
        parent_session_id=parent_session_id,
    )
    session.execute.return_value = _mock_scalar_result([child_session])

    results = await service.list_sessions(
        created_by=role.user_id,
        parent_session_id=parent_session_id,
        limit=1,
    )

    session.execute.assert_awaited_once()
    assert results == [
        AgentSessionRead.model_validate(child_session, from_attributes=True)
    ]
