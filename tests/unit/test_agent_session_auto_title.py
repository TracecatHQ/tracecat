"""Tests for AgentSessionService first-prompt auto-title behavior."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.agent.session.service import AgentSessionService
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.models import AgentSession
from tracecat.exceptions import TracecatNotFoundError


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


@pytest.fixture
def user_role() -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute", "secret:read"}),
    )


class _DummyAgentManagementService:
    def __init__(self, *args, **kwargs):
        pass

    @asynccontextmanager
    async def with_model_config(self, *, use_workspace_credentials: bool = False):
        assert use_workspace_credentials is False
        yield SimpleNamespace(name="gpt-4o-mini", provider="openai")


@pytest.mark.anyio
async def test_auto_title_updates_session_on_first_prompt(role: Role) -> None:
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(
        scalar_one_or_none=lambda: uuid.uuid4()
    )
    service = AgentSessionService(session, role)
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="New Chat",
        entity_type="case",
        entity_id=uuid.uuid4(),
    )
    agent_session.id = uuid.uuid4()

    service._is_first_prompt_for_session = AsyncMock(return_value=True)

    with (
        patch(
            "tracecat.agent.session.service.AgentManagementService",
            _DummyAgentManagementService,
        ),
        patch(
            "tracecat.agent.session.service.generate_session_title",
            AsyncMock(return_value="Investigate login failures"),
        ),
    ):
        await service.auto_title_session_on_first_prompt(
            agent_session,
            "Users cannot sign in",
        )

    assert agent_session.title == "Investigate login failures"
    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_auto_title_uses_service_role_for_model_config(user_role: Role) -> None:
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(
        scalar_one_or_none=lambda: uuid.uuid4()
    )
    service = AgentSessionService(session, user_role)
    agent_session = AgentSession(
        workspace_id=user_role.workspace_id,
        title="New Chat",
        entity_type="workflow",
        entity_id=uuid.uuid4(),
    )
    agent_session.id = uuid.uuid4()
    service._is_first_prompt_for_session = AsyncMock(return_value=True)

    captured_roles: list[Role] = []

    class _CapturingAgentManagementService:
        def __init__(self, *args, **kwargs):
            role = kwargs.get("role", args[1] if len(args) > 1 else None)
            assert role is not None
            captured_roles.append(role)

        @asynccontextmanager
        async def with_model_config(self, *, use_workspace_credentials: bool = False):
            assert use_workspace_credentials is False
            yield SimpleNamespace(name="gpt-4o-mini", provider="openai")

    with (
        patch(
            "tracecat.agent.session.service.AgentManagementService",
            _CapturingAgentManagementService,
        ),
        patch(
            "tracecat.agent.session.service.generate_session_title",
            AsyncMock(return_value="Investigate login failures"),
        ),
    ):
        await service.auto_title_session_on_first_prompt(
            agent_session,
            "Users cannot sign in",
        )

    assert len(captured_roles) == 1
    auto_title_role = captured_roles[0]
    assert auto_title_role.type == "service"
    assert auto_title_role.service_id == "tracecat-api"
    assert auto_title_role.workspace_id == user_role.workspace_id
    assert auto_title_role.organization_id == user_role.organization_id
    assert auto_title_role.scopes == SERVICE_PRINCIPAL_SCOPES["tracecat-api"]


@pytest.mark.anyio
async def test_auto_title_skips_when_not_first_prompt(role: Role) -> None:
    session = AsyncMock()
    service = AgentSessionService(session, role)
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="Existing title",
        entity_type="copilot",
        entity_id=uuid.uuid4(),
    )
    agent_session.id = uuid.uuid4()

    service._is_first_prompt_for_session = AsyncMock(return_value=False)

    await service.auto_title_session_on_first_prompt(agent_session, "Do something")

    session.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_auto_title_does_not_raise_on_expected_generation_error(
    role: Role,
) -> None:
    session = AsyncMock()
    service = AgentSessionService(session, role)
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="New Chat",
        entity_type="workflow",
        entity_id=uuid.uuid4(),
    )
    agent_session.id = uuid.uuid4()

    service._is_first_prompt_for_session = AsyncMock(return_value=True)

    with (
        patch(
            "tracecat.agent.session.service.AgentManagementService",
            _DummyAgentManagementService,
        ),
        patch(
            "tracecat.agent.session.service.generate_session_title",
            AsyncMock(side_effect=TracecatNotFoundError("model missing")),
        ),
    ):
        await service.auto_title_session_on_first_prompt(
            agent_session,
            "Find issue",
        )

    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_auto_title_raises_on_unexpected_generation_error(role: Role) -> None:
    session = AsyncMock()
    service = AgentSessionService(session, role)
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="New Chat",
        entity_type="workflow",
        entity_id=uuid.uuid4(),
    )
    agent_session.id = uuid.uuid4()

    service._is_first_prompt_for_session = AsyncMock(return_value=True)

    with (
        patch(
            "tracecat.agent.session.service.AgentManagementService",
            _DummyAgentManagementService,
        ),
        patch(
            "tracecat.agent.session.service.generate_session_title",
            AsyncMock(side_effect=RuntimeError("provider down")),
        ),
        pytest.raises(RuntimeError, match="provider down"),
    ):
        await service.auto_title_session_on_first_prompt(
            agent_session,
            "Find issue",
        )


@pytest.mark.anyio
async def test_auto_title_skips_empty_prompt(role: Role) -> None:
    session = AsyncMock()
    service = AgentSessionService(session, role)
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="New Chat",
        entity_type="case",
        entity_id=uuid.uuid4(),
    )
    agent_session.id = uuid.uuid4()

    await service.auto_title_session_on_first_prompt(agent_session, "   ")

    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_auto_title_skips_when_compare_and_set_guard_fails(role: Role) -> None:
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    service = AgentSessionService(session, role)
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="New Chat",
        entity_type="approval",
        entity_id=uuid.uuid4(),
    )
    agent_session.id = uuid.uuid4()

    service._is_first_prompt_for_session = AsyncMock(return_value=True)

    with (
        patch(
            "tracecat.agent.session.service.AgentManagementService",
            _DummyAgentManagementService,
        ),
        patch(
            "tracecat.agent.session.service.generate_session_title",
            AsyncMock(return_value="Approval follow-up investigation"),
        ),
    ):
        await service.auto_title_session_on_first_prompt(
            agent_session,
            "Investigate this approval request",
        )

    session.refresh.assert_awaited_once_with(agent_session)
