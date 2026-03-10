from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tracecat.agent.session.schemas import AgentSessionUpdate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute", "agent:read"}),
    )


@pytest.mark.anyio
async def test_build_agent_config_uses_session_model_catalog_ref(role: Role) -> None:
    session = AsyncMock()
    session.add = Mock()
    service = AgentSessionService(session, role)
    session_model_catalog_ref = "default_sidecar:default:abc:gpt-5"
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="New Chat",
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
        model_catalog_ref=session_model_catalog_ref,
        tools=["core.http_request"],
    )

    class _FakeAgentManagementService:
        def __init__(self, *_args, **_kwargs):
            pass

        @asynccontextmanager
        async def with_model_config(self, *, catalog_ref: str | None = None):
            assert catalog_ref == session_model_catalog_ref
            yield AgentConfig(
                model_name="gpt-5",
                model_provider="default_sidecar",
                model_catalog_ref=session_model_catalog_ref,
            )

    with patch(
        "tracecat.agent.session.service.AgentManagementService",
        _FakeAgentManagementService,
    ):
        async with service._build_agent_config(agent_session) as config:
            assert config.model_name == "gpt-5"
            assert config.model_provider == "default_sidecar"
            assert config.model_catalog_ref == session_model_catalog_ref
            assert config.actions == ["core.http_request"]


@pytest.mark.anyio
async def test_update_session_clears_model_catalog_ref_when_preset_selected(
    role: Role,
) -> None:
    session = AsyncMock()
    session.add = Mock()
    service = AgentSessionService(session, role)
    preset_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="New Chat",
        entity_type=AgentSessionEntity.CASE,
        entity_id=uuid.uuid4(),
        model_catalog_ref="default_sidecar:default:abc:gpt-5",
    )

    fake_preset_service = SimpleNamespace(
        get_preset=AsyncMock(return_value=object()),
        resolve_agent_preset_version=AsyncMock(
            return_value=SimpleNamespace(id=uuid.uuid4())
        ),
    )

    with patch(
        "tracecat.agent.session.service.AgentPresetService",
        return_value=fake_preset_service,
    ):
        updated = await service.update_session(
            agent_session,
            params=AgentSessionUpdate(agent_preset_id=preset_id),
        )

    assert updated.agent_preset_id == preset_id
    assert updated.model_catalog_ref is None
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(agent_session)


@pytest.mark.anyio
async def test_update_session_rejects_model_catalog_ref_when_preset_exists(
    role: Role,
) -> None:
    session = AsyncMock()
    session.add = Mock()
    service = AgentSessionService(session, role)
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="New Chat",
        entity_type=AgentSessionEntity.CASE,
        entity_id=uuid.uuid4(),
        agent_preset_id=uuid.uuid4(),
    )

    with pytest.raises(ValueError, match="model_catalog_ref cannot be set"):
        await service.update_session(
            agent_session,
            params=AgentSessionUpdate(
                model_catalog_ref="default_sidecar:default:abc:gpt-5"
            ),
        )


@pytest.mark.anyio
async def test_fork_session_copies_parent_model_catalog_ref(role: Role) -> None:
    session = AsyncMock()
    session.add = Mock()
    service = AgentSessionService(session, role)
    parent_session_id = uuid.uuid4()
    parent = AgentSession(
        id=parent_session_id,
        workspace_id=role.workspace_id,
        title="Case chat",
        entity_type=AgentSessionEntity.CASE,
        entity_id=uuid.uuid4(),
        model_catalog_ref="default_sidecar:default:abc:gpt-5",
        tools=["core.http_request"],
    )
    service.get_session = AsyncMock(return_value=parent)

    forked = await service.fork_session(parent_session_id)

    assert forked.model_catalog_ref == parent.model_catalog_ref
    assert forked.agent_preset_id is None
    assert forked.parent_session_id == parent_session_id
    session.add.assert_called_once_with(forked)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(forked)
