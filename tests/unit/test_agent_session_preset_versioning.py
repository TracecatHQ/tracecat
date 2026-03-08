from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.agent.session.schemas import AgentSessionUpdate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession


def _build_service() -> tuple[AgentSessionService, SimpleNamespace, Role]:
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )
    session = SimpleNamespace(
        add=Mock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    return AgentSessionService(cast(Any, session), role), session, role


@pytest.mark.anyio
async def test_update_session_resolves_current_version_when_preset_changes() -> None:
    service, session, role = _build_service()
    old_preset_id = uuid.uuid4()
    new_preset_id = uuid.uuid4()
    old_version_id = uuid.uuid4()
    resolved_version_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="Chat",
        created_by=uuid.uuid4(),
        entity_type="case",
        entity_id=uuid.uuid4(),
        agent_preset_id=old_preset_id,
        agent_preset_version_id=old_version_id,
    )
    resolve_mock = AsyncMock(return_value=resolved_version_id)
    service._resolve_preset_version_for_assignment = resolve_mock

    updated = await service.update_session(
        agent_session,
        params=AgentSessionUpdate(agent_preset_id=new_preset_id),
    )

    resolve_mock.assert_awaited_once_with(
        entity_type=AgentSessionEntity.CASE,
        entity_id=agent_session.entity_id,
        agent_preset_id=new_preset_id,
        agent_preset_version_id=None,
    )
    assert updated.agent_preset_id == new_preset_id
    assert updated.agent_preset_version_id == resolved_version_id
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(agent_session)


@pytest.mark.anyio
async def test_update_session_allows_version_only_repin_for_preset_sessions() -> None:
    service, session, role = _build_service()
    preset_id = uuid.uuid4()
    new_version_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="Chat",
        created_by=uuid.uuid4(),
        entity_type="agent_preset",
        entity_id=preset_id,
        agent_preset_id=None,
        agent_preset_version_id=uuid.uuid4(),
    )
    resolve_mock = AsyncMock(return_value=new_version_id)
    service._resolve_preset_version_for_assignment = resolve_mock

    updated = await service.update_session(
        agent_session,
        params=AgentSessionUpdate(agent_preset_version_id=new_version_id),
    )

    resolve_mock.assert_awaited_once_with(
        entity_type=AgentSessionEntity.AGENT_PRESET,
        entity_id=preset_id,
        agent_preset_id=None,
        agent_preset_version_id=new_version_id,
    )
    assert updated.agent_preset_id is None
    assert updated.agent_preset_version_id == new_version_id
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(agent_session)
