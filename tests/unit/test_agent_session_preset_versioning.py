from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tracecat.agent.session.schemas import AgentSessionCreate, AgentSessionUpdate
from tracecat.agent.session.service import (
    AGENT_SESSION_EXECUTION_SCOPES,
    AgentSessionService,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.chat.tools import WORKSPACE_CHAT_DEFAULT_TOOLS
from tracecat.db.models import AgentSession
from tracecat.exceptions import TracecatValidationError
from tracecat.tiers.enums import Entitlement


class _TestAgentSessionService(AgentSessionService):
    agent_addons_enabled: bool = True
    entitlement_checks: list[Entitlement]

    def __init__(self, session: Any, role: Role) -> None:
        super().__init__(session, role)
        self.entitlement_checks = []

    async def has_entitlement(self, entitlement: Entitlement) -> bool:
        self.entitlement_checks.append(entitlement)
        if entitlement is Entitlement.AGENT_ADDONS:
            return self.agent_addons_enabled
        return True


def _build_service() -> tuple[_TestAgentSessionService, SimpleNamespace, Role]:
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        # action:*:execute lets _resolve_workspace_chat_actions keep the full
        # default tool set; per-action scope filtering is covered independently
        # in test_chat_tools.py.
        scopes=frozenset({"agent:execute", "action:*:execute"}),
    )
    session = SimpleNamespace(
        add=Mock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    service = _TestAgentSessionService(cast(Any, session), role)
    return service, session, role


def test_execution_role_adds_only_agent_runtime_scopes() -> None:
    service, _session, actor_role = _build_service()

    execution_role = service.execution_role

    assert execution_role.scopes == (
        (actor_role.scopes or frozenset()) | AGENT_SESSION_EXECUTION_SCOPES
    )
    assert actor_role.scopes == frozenset({"agent:execute", "action:*:execute"})
    assert execution_role.user_id == actor_role.user_id
    assert execution_role.workspace_id == actor_role.workspace_id
    assert execution_role.organization_id == actor_role.organization_id
    assert AGENT_SESSION_EXECUTION_SCOPES == frozenset(
        {
            "action:*:execute",
            "agent:read",
            "org:secret:read",
            "secret:read",
            "workflow:execute",
        }
    )


@pytest.mark.anyio
async def test_create_session_preserves_null_preset_version_for_current() -> None:
    service, session, _role = _build_service()
    preset_id = uuid.uuid4()
    agent_session_id = uuid.uuid4()
    validate_mock = AsyncMock(return_value=None)
    agents_binding_mock = AsyncMock(return_value=None)
    service._validate_preset_version_for_assignment = validate_mock
    service._resolve_agents_binding_for_preset_version_id = agents_binding_mock

    created = await service.create_session(
        AgentSessionCreate(
            id=agent_session_id,
            title="Chat",
            entity_type=AgentSessionEntity.AGENT_PRESET,
            entity_id=preset_id,
            agent_preset_version_id=None,
        )
    )

    validate_mock.assert_awaited_once_with(
        entity_type=AgentSessionEntity.AGENT_PRESET,
        entity_id=preset_id,
        agent_preset_id=None,
        agent_preset_version_id=None,
    )
    agents_binding_mock.assert_awaited_once_with(None)
    assert created.agent_preset_id == preset_id
    assert created.agent_preset_version_id is None
    assert created.agents_binding is None
    session.add.assert_called_once_with(created)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(created)


@pytest.mark.anyio
async def test_create_workspace_chat_session_applies_current_default_tools() -> None:
    service, session, _role = _build_service()
    validate_mock = AsyncMock(return_value=None)
    agents_binding_mock = AsyncMock(return_value=None)
    service._validate_preset_version_for_assignment = validate_mock
    service._resolve_agents_binding_for_preset_version_id = agents_binding_mock

    created = await service.create_session(
        AgentSessionCreate(
            title="Chat",
            entity_type=AgentSessionEntity.WORKSPACE_CHAT,
            entity_id=uuid.uuid4(),
        )
    )

    # Workspace chat no longer freezes defaults into the session; they are
    # merged at runtime so the session always reflects the current defaults.
    assert created.tools is None
    assert (
        await service._resolve_workspace_chat_actions(created)
        == WORKSPACE_CHAT_DEFAULT_TOOLS
    )
    session.add.assert_called_once_with(created)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(created)


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_session_mcp_integrations_must_load_before_persisting(
    operation: str,
) -> None:
    """Selected MCP servers must load for the workspace before a session stores them."""
    service, session, role = _build_service()
    assert role.workspace_id is not None
    mcp_integrations = [str(uuid.uuid4())]
    preset_service = Mock()
    preset_service.load_selected_mcp_integrations = AsyncMock(
        side_effect=TracecatValidationError("MCP integration not found")
    )
    service._validate_preset_version_for_assignment = AsyncMock(return_value=None)
    service._resolve_agents_binding_for_preset_version_id = AsyncMock(return_value=None)

    with (
        patch(
            "tracecat.agent.session.service.AgentPresetService",
            Mock(return_value=preset_service),
        ),
        pytest.raises(TracecatValidationError, match="MCP integration not found"),
    ):
        if operation == "create":
            await service.create_session(
                AgentSessionCreate(
                    title="Chat",
                    entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                    entity_id=uuid.uuid4(),
                    mcp_integrations=mcp_integrations,
                )
            )
        else:
            await service.update_session(
                AgentSession(
                    workspace_id=role.workspace_id,
                    entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
                    entity_id=role.workspace_id,
                    mcp_integrations=[],
                ),
                params=AgentSessionUpdate(mcp_integrations=mcp_integrations),
            )

    preset_service.load_selected_mcp_integrations.assert_awaited_once_with(
        mcp_integrations
    )
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_update_session_clears_agents_binding_when_preset_removed() -> None:
    service, session, role = _build_service()
    old_preset_id = uuid.uuid4()
    old_version_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="Chat",
        created_by=uuid.uuid4(),
        entity_type="case",
        entity_id=uuid.uuid4(),
        agent_preset_id=old_preset_id,
        agent_preset_version_id=old_version_id,
        agents_binding={"subagents": []},
    )

    updated = await service.update_session(
        agent_session,
        params=AgentSessionUpdate(agent_preset_id=None),
    )

    assert updated.agent_preset_id is None
    assert updated.agent_preset_version_id is None
    assert updated.agents_binding is None
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
    validate_mock = AsyncMock(return_value=new_version_id)
    agents_binding_mock = AsyncMock(return_value={"subagents": []})
    service._validate_preset_version_for_assignment = validate_mock
    service._resolve_agents_binding_for_preset_version_id = agents_binding_mock

    updated = await service.update_session(
        agent_session,
        params=AgentSessionUpdate(agent_preset_version_id=new_version_id),
    )

    validate_mock.assert_awaited_once_with(
        entity_type=AgentSessionEntity.AGENT_PRESET,
        entity_id=preset_id,
        agent_preset_id=None,
        agent_preset_version_id=new_version_id,
    )
    assert updated.agent_preset_id == preset_id
    assert updated.agent_preset_version_id == new_version_id
    assert updated.agents_binding == {"subagents": []}
    agents_binding_mock.assert_awaited_once_with(new_version_id)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(agent_session)


@pytest.mark.anyio
async def test_update_session_clears_pinned_version_to_follow_current() -> None:
    service, session, role = _build_service()
    preset_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="Chat",
        created_by=uuid.uuid4(),
        entity_type="agent_preset",
        entity_id=preset_id,
        agent_preset_id=preset_id,
        agent_preset_version_id=uuid.uuid4(),
        agents_binding={"subagents": []},
    )
    validate_mock = AsyncMock()
    agents_binding_mock = AsyncMock(return_value=None)
    service._validate_preset_version_for_assignment = validate_mock
    service._resolve_agents_binding_for_preset_version_id = agents_binding_mock

    updated = await service.update_session(
        agent_session,
        params=AgentSessionUpdate(agent_preset_version_id=None),
    )

    validate_mock.assert_not_awaited()
    assert updated.agent_preset_id == preset_id
    assert updated.agent_preset_version_id is None
    assert updated.agents_binding is None
    agents_binding_mock.assert_awaited_once_with(None)
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


def _restricted_service() -> _TestAgentSessionService:
    """Build a service whose role can start a chat but lacks broad action scopes.

    ``agent:execute`` alone lets the user open a workspace-chat session; the only
    action scope granted is ``core.workflow.get_workflow``. Anything else the
    attached preset exposes must be dropped before the config is yielded.
    """
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset(
            {"agent:execute", "action:core.workflow.get_workflow:execute"}
        ),
    )
    session = SimpleNamespace(add=Mock(), commit=AsyncMock(), refresh=AsyncMock())
    return _TestAgentSessionService(cast(Any, session), role)


@pytest.mark.anyio
async def test_workspace_chat_preset_config_scope_filters_actions() -> None:
    """A preset attached to workspace chat must not smuggle unauthorized tools.

    Regression: the preset branch of ``_build_agent_config`` previously yielded
    the preset config verbatim, so a user with only ``agent:execute`` could
    attach a preset exposing privileged actions (e.g. ``edit_workflow``,
    ``delete_case``) and run them under the executor service principal, bypassing
    the user-scope gate that the no-preset path applies.
    """
    service = _restricted_service()
    workspace_id = service.role.workspace_id
    assert workspace_id is not None
    preset_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=workspace_id,
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=workspace_id,
        agent_preset_id=preset_id,
    )

    preset_config = AgentConfig(
        model_name="test-model",
        model_provider="test-provider",
        instructions="preset instructions",
        actions=[
            "core.workflow.get_workflow",
            "core.workflow.edit_workflow",
            "core.cases.delete_case",
        ],
    )

    @contextlib.asynccontextmanager
    async def _fake_with_preset_config(**_kwargs: Any):
        yield preset_config

    service._entity_to_prompt = AsyncMock(return_value="entity instructions")  # type: ignore[method-assign]
    service._resolve_builtin_workspace_chat_skills = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with patch(
        "tracecat.agent.session.service.AgentManagementService"
    ) as agent_svc_cls:
        agent_svc_cls.return_value = SimpleNamespace(
            with_preset_config=_fake_with_preset_config
        )
        async with service._build_agent_config(agent_session) as resolved:
            # Only the one action the user is scoped for survives; the privileged
            # workflow-edit and case-delete tools are stripped.
            assert resolved.actions == ["core.workflow.get_workflow"]
            # Instructions still combine preset + entity context.
            assert resolved.instructions == "preset instructions\n\nentity instructions"

        execution_role = agent_svc_cls.call_args.args[1]
        assert execution_role.scopes == (
            (service.role.scopes or frozenset()) | AGENT_SESSION_EXECUTION_SCOPES
        )
        assert service.role.scopes == frozenset(
            {"agent:execute", "action:core.workflow.get_workflow:execute"}
        )


@pytest.mark.anyio
async def test_workspace_chat_preset_config_superuser_keeps_all_actions() -> None:
    """A fully-scoped caller retains every action the preset exposes."""
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute", "action:*:execute"}),
    )
    session = SimpleNamespace(add=Mock(), commit=AsyncMock(), refresh=AsyncMock())
    service = _TestAgentSessionService(cast(Any, session), role)
    workspace_id = role.workspace_id
    assert workspace_id is not None
    agent_session = AgentSession(
        workspace_id=workspace_id,
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=workspace_id,
        agent_preset_id=uuid.uuid4(),
    )

    actions = ["core.workflow.edit_workflow", "core.cases.delete_case"]
    preset_config = AgentConfig(
        model_name="test-model",
        model_provider="test-provider",
        instructions="preset instructions",
        actions=actions,
    )

    @contextlib.asynccontextmanager
    async def _fake_with_preset_config(**_kwargs: Any):
        yield preset_config

    service._entity_to_prompt = AsyncMock(return_value="entity instructions")  # type: ignore[method-assign]
    service._resolve_builtin_workspace_chat_skills = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with patch(
        "tracecat.agent.session.service.AgentManagementService"
    ) as agent_svc_cls:
        agent_svc_cls.return_value = SimpleNamespace(
            with_preset_config=_fake_with_preset_config
        )
        async with service._build_agent_config(agent_session) as resolved:
            assert resolved.actions == actions
