from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.agent.session.schemas import AgentSessionUpdate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.skill.types import ResolvedSkillRef
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession
from tracecat.exceptions import TracecatValidationError


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
async def test_ensure_session_preset_version_id_uses_current_version() -> None:
    service, session, role = _build_service()
    preset_id = uuid.uuid4()
    resolved_version_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="Chat",
        created_by=uuid.uuid4(),
        entity_type="case",
        entity_id=uuid.uuid4(),
        agent_preset_id=preset_id,
        agent_preset_version_id=None,
    )
    resolve_agent_preset_version = AsyncMock(
        return_value=SimpleNamespace(id=resolved_version_id)
    )

    preset_service = Mock()
    preset_service.resolve_agent_preset_version = resolve_agent_preset_version

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tracecat.agent.session.service.AgentPresetService",
            Mock(return_value=preset_service),
        )

        resolved = await service._ensure_session_preset_version_id(agent_session)

    assert resolved == resolved_version_id
    assert agent_session.agent_preset_version_id == resolved_version_id
    resolve_agent_preset_version.assert_awaited_once_with(
        preset_id=preset_id,
    )
    session.add.assert_called_with(agent_session)
    session.commit.assert_awaited_once()


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
    assert updated.agent_preset_id == preset_id
    assert updated.agent_preset_version_id == new_version_id
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(agent_session)


@pytest.mark.anyio
async def test_update_session_ignores_mismatched_preset_id_for_preset_sessions() -> (
    None
):
    service, session, role = _build_service()
    preset_id = uuid.uuid4()
    mismatched_preset_id = uuid.uuid4()
    new_version_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="Chat",
        created_by=uuid.uuid4(),
        entity_type="agent_preset",
        entity_id=preset_id,
        agent_preset_id=preset_id,
        agent_preset_version_id=uuid.uuid4(),
    )
    resolve_mock = AsyncMock(return_value=new_version_id)
    service._resolve_preset_version_for_assignment = resolve_mock

    updated = await service.update_session(
        agent_session,
        params=AgentSessionUpdate(
            agent_preset_id=mismatched_preset_id,
            agent_preset_version_id=new_version_id,
        ),
    )

    resolve_mock.assert_awaited_once_with(
        entity_type=AgentSessionEntity.AGENT_PRESET,
        entity_id=preset_id,
        agent_preset_id=mismatched_preset_id,
        agent_preset_version_id=new_version_id,
    )
    assert updated.agent_preset_id == preset_id
    assert updated.agent_preset_version_id == new_version_id
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(agent_session)


@pytest.mark.anyio
async def test_update_session_skips_entity_type_parsing_for_unrelated_updates() -> None:
    service, session, role = _build_service()
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="Chat",
        created_by=uuid.uuid4(),
        entity_type="not-a-real-entity",
        entity_id=uuid.uuid4(),
    )

    updated = await service.update_session(
        agent_session,
        params=AgentSessionUpdate(title="Renamed chat"),
    )

    assert updated.title == "Renamed chat"
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(agent_session)


@pytest.mark.anyio
async def test_update_session_rejects_preset_updates_for_invalid_entity_type() -> None:
    service, session, role = _build_service()
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="Chat",
        created_by=uuid.uuid4(),
        entity_type="not-a-real-entity",
        entity_id=uuid.uuid4(),
    )

    with pytest.raises(
        TracecatValidationError,
        match="Cannot update preset assignment for a session with an invalid entity type",
    ):
        await service.update_session(
            agent_session,
            params=AgentSessionUpdate(agent_preset_version_id=uuid.uuid4()),
        )

    session.commit.assert_not_awaited()
    session.refresh.assert_not_awaited()


@pytest.mark.anyio
async def test_build_agent_config_supports_skill_playground_sessions() -> None:
    service, _, role = _build_service()
    skill_id = uuid.uuid4()
    skill_version_id = uuid.uuid4()
    resolved_skill = ResolvedSkillRef(
        skill_id=skill_id,
        skill_slug="triage-skill",
        skill_version_id=skill_version_id,
        manifest_sha256="abc123",
    )
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="Skill chat",
        created_by=uuid.uuid4(),
        entity_type="skill",
        entity_id=skill_id,
        channel_context={
            "skill_playground": {
                "skill_id": str(skill_id),
                "skill_slug": "triage-skill",
                "skill_version_id": str(skill_version_id),
                "system_prompt": "Test this skill carefully.",
                "mcp_integration_ids": [],
            }
        },
    )

    class _AgentManagementService:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        @asynccontextmanager
        async def with_model_config(self, *, use_workspace_credentials: bool = False):
            assert use_workspace_credentials is True
            yield SimpleNamespace(name="gpt-4o-mini", provider="openai")

        @asynccontextmanager
        async def with_runtime_agent_config(self, agent_config, **_kwargs: Any):
            yield agent_config

    class _SkillService:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def get_resolved_skill_ref(
            self, *, skill_id: uuid.UUID, skill_version_id: uuid.UUID
        ) -> ResolvedSkillRef:
            assert skill_id == resolved_skill.skill_id
            assert skill_version_id == resolved_skill.skill_version_id
            return resolved_skill

    class _PresetService:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def resolve_mcp_integrations(self, _mcp_ids: list[str] | None):
            return None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tracecat.agent.session.service.AgentManagementService",
            _AgentManagementService,
        )
        mp.setattr(
            "tracecat.agent.session.service.SkillService",
            _SkillService,
        )
        mp.setattr(
            "tracecat.agent.session.service.AgentPresetService",
            _PresetService,
        )

        async with service._build_agent_config(agent_session) as config:
            assert config.model_name == "gpt-4o-mini"
            assert config.model_provider == "openai"
            assert config.instructions == "Test this skill carefully."
            assert config.resolved_skills == [resolved_skill]
            assert config.mcp_servers is None
