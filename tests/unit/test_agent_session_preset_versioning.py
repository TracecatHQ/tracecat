from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tracecat.agent.preset.resolved_refs import ResolvedRef, ResolvedRefs
from tracecat.agent.session.schemas import AgentSessionCreate, AgentSessionUpdate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.subagents import ResolvedAgentsConfig
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.chat.tools import WORKSPACE_CHAT_DEFAULT_TOOLS, get_default_tools
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
async def test_create_workspace_chat_session_omits_agent_tools_without_entitlement() -> (
    None
):
    service, session, _role = _build_service()
    service.agent_addons_enabled = False
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

    # Defaults are resolved at runtime; without the agent addon entitlement the
    # agent preset tools are filtered out of the merged result.
    assert created.tools is None
    resolved = await service._resolve_workspace_chat_actions(created)
    assert resolved == get_default_tools(
        AgentSessionEntity.WORKSPACE_CHAT.value,
        agent_addons_enabled=False,
    )
    assert Entitlement.AGENT_ADDONS in service.entitlement_checks
    session.add.assert_called_once_with(created)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(created)


@pytest.mark.anyio
async def test_create_session_validates_mcp_integrations_before_persisting() -> None:
    service, session, _role = _build_service()
    mcp_integrations = [str(uuid.uuid4())]
    validate_mcp_mock = AsyncMock(return_value=None)
    validate_preset_mock = AsyncMock(return_value=None)
    agents_binding_mock = AsyncMock(return_value=None)
    service._validate_session_mcp_integrations = validate_mcp_mock
    service._validate_preset_version_for_assignment = validate_preset_mock
    service._resolve_agents_binding_for_preset_version_id = agents_binding_mock

    created = await service.create_session(
        AgentSessionCreate(
            title="Chat",
            entity_type=AgentSessionEntity.WORKSPACE_CHAT,
            entity_id=uuid.uuid4(),
            mcp_integrations=mcp_integrations,
        )
    )

    validate_mcp_mock.assert_awaited_once_with(mcp_integrations)
    assert created.mcp_integrations == mcp_integrations
    session.add.assert_called_once_with(created)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(created)


@pytest.mark.anyio
async def test_update_session_validates_mcp_integrations_before_persisting() -> None:
    service, session, role = _build_service()
    assert role.workspace_id is not None
    mcp_integrations = [str(uuid.uuid4())]
    validate_mcp_mock = AsyncMock(return_value=None)
    service._validate_session_mcp_integrations = validate_mcp_mock
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=role.workspace_id,
        mcp_integrations=[],
    )

    updated = await service.update_session(
        agent_session,
        params=AgentSessionUpdate(mcp_integrations=mcp_integrations),
    )

    validate_mcp_mock.assert_awaited_once_with(mcp_integrations)
    assert updated.mcp_integrations == mcp_integrations
    session.add.assert_called_once_with(updated)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(updated)


@pytest.mark.anyio
async def test_resolve_session_mcp_servers_requires_agent_addons_entitlement() -> None:
    service, _session, role = _build_service()
    service.agent_addons_enabled = False
    assert role.workspace_id is not None
    mcp_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=role.workspace_id,
        mcp_integrations=[str(mcp_id)],
    )
    resolver = AsyncMock(
        return_value=[
            {
                "type": "http",
                "name": "RunReveal",
                "url": "https://mcp.example.test",
            }
        ]
    )
    agent_svc = SimpleNamespace(
        presets=SimpleNamespace(resolve_mcp_integration_refs=resolver)
    )

    result = await service._resolve_session_mcp_servers(
        agent_session,
        cast(Any, agent_svc),
    )

    assert result is None
    resolver.assert_not_awaited()
    assert Entitlement.AGENT_ADDONS in service.entitlement_checks


@pytest.mark.anyio
async def test_create_session_derives_binding_from_selected_preset_version() -> None:
    service, session, _role = _build_service()
    preset_id = uuid.uuid4()
    selected_version_id = uuid.uuid4()
    agents_binding = {"enabled": True, "subagents": []}
    validate_mock = AsyncMock(return_value=selected_version_id)
    agents_binding_mock = AsyncMock(return_value=agents_binding)
    service._validate_preset_version_for_assignment = validate_mock
    service._resolve_agents_binding_for_preset_version_id = agents_binding_mock

    created = await service.create_session(
        AgentSessionCreate(
            title="Chat",
            entity_type=AgentSessionEntity.CASE,
            entity_id=uuid.uuid4(),
            agent_preset_id=preset_id,
            agent_preset_version_id=selected_version_id,
        )
    )

    validate_mock.assert_awaited_once_with(
        entity_type=AgentSessionEntity.CASE,
        entity_id=created.entity_id,
        agent_preset_id=preset_id,
        agent_preset_version_id=selected_version_id,
    )
    assert created.agent_preset_id == preset_id
    assert created.agent_preset_version_id == selected_version_id
    assert created.agents_binding == agents_binding
    agents_binding_mock.assert_awaited_once_with(selected_version_id)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(created)


@pytest.mark.anyio
async def test_create_session_prefers_provided_binding_for_selected_version() -> None:
    service, session, _role = _build_service()
    preset_id = uuid.uuid4()
    selected_version_id = uuid.uuid4()
    child_preset_id = uuid.uuid4()
    child_version_id = uuid.uuid4()
    validate_mock = AsyncMock(return_value=selected_version_id)
    agents_binding_mock = AsyncMock(
        side_effect=AssertionError("should not derive binding when provided")
    )
    service._validate_preset_version_for_assignment = validate_mock
    service._resolve_agents_binding_for_preset_version_id = agents_binding_mock
    agents_binding = ResolvedAgentsConfig.model_validate(
        {
            "enabled": True,
            "subagents": [
                {
                    "preset": "child",
                    "preset_version": 1,
                    "preset_id": child_preset_id,
                    "preset_version_id": child_version_id,
                }
            ],
        }
    )

    created = await service.create_session(
        AgentSessionCreate(
            title="Chat",
            entity_type=AgentSessionEntity.CASE,
            entity_id=uuid.uuid4(),
            agent_preset_id=preset_id,
            agent_preset_version_id=selected_version_id,
        ),
        agents_binding=agents_binding,
    )

    validate_mock.assert_awaited_once_with(
        entity_type=AgentSessionEntity.CASE,
        entity_id=created.entity_id,
        agent_preset_id=preset_id,
        agent_preset_version_id=selected_version_id,
    )
    assert created.agent_preset_id == preset_id
    assert created.agent_preset_version_id == selected_version_id
    assert created.agents_binding == agents_binding.model_dump(mode="json")
    agents_binding_mock.assert_not_called()
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(created)


@pytest.mark.anyio
async def test_create_session_persists_internal_agents_binding_without_preset() -> None:
    service, session, _role = _build_service()
    validate_mock = AsyncMock(return_value=None)
    agents_binding_mock = AsyncMock(return_value=None)
    service._validate_preset_version_for_assignment = validate_mock
    service._resolve_agents_binding_for_preset_version_id = agents_binding_mock
    agents_binding = ResolvedAgentsConfig.model_validate(
        {"enabled": True, "subagents": []}
    )

    created = await service.create_session(
        AgentSessionCreate(
            title="Chat",
            entity_type=AgentSessionEntity.CASE,
            entity_id=uuid.uuid4(),
        ),
        agents_binding=agents_binding,
    )

    assert created.agents_binding == {"enabled": True, "subagents": []}
    agents_binding_mock.assert_not_called()
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(created)


@pytest.mark.anyio
async def test_update_session_preserves_null_version_when_preset_changes() -> None:
    service, session, role = _build_service()
    old_preset_id = uuid.uuid4()
    new_preset_id = uuid.uuid4()
    old_version_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="Chat",
        created_by=uuid.uuid4(),
        entity_type="case",
        entity_id=uuid.uuid4(),
        agent_preset_id=old_preset_id,
        agent_preset_version_id=old_version_id,
        agents_binding={"enabled": True, "subagents": []},
    )
    validate_mock = AsyncMock(return_value=None)
    agents_binding_mock = AsyncMock(return_value=None)
    service._validate_preset_version_for_assignment = validate_mock
    service._resolve_agents_binding_for_preset_version_id = agents_binding_mock

    updated = await service.update_session(
        agent_session,
        params=AgentSessionUpdate(agent_preset_id=new_preset_id),
    )

    validate_mock.assert_awaited_once_with(
        entity_type=AgentSessionEntity.CASE,
        entity_id=agent_session.entity_id,
        agent_preset_id=new_preset_id,
        agent_preset_version_id=None,
    )
    assert updated.agent_preset_id == new_preset_id
    assert updated.agent_preset_version_id is None
    assert updated.agents_binding is None
    agents_binding_mock.assert_awaited_once_with(None)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(agent_session)


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
        agents_binding={"enabled": True, "subagents": []},
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
async def test_validate_preset_assignment_preserves_null_without_resolving_current() -> (
    None
):
    service, _session, _role = _build_service()
    preset_id = uuid.uuid4()

    preset_service = Mock()
    preset_service.get_preset = AsyncMock(return_value=SimpleNamespace(id=preset_id))
    preset_service.resolve_agent_preset_version = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "tracecat.agent.session.service.AgentPresetService",
            Mock(return_value=preset_service),
        )

        selected_version_id = await service._validate_preset_version_for_assignment(
            entity_type=AgentSessionEntity.AGENT_PRESET,
            entity_id=preset_id,
            agent_preset_id=None,
            agent_preset_version_id=None,
        )

    assert selected_version_id is None
    preset_service.get_preset.assert_awaited_once_with(preset_id)
    preset_service.resolve_agent_preset_version.assert_not_awaited()


@pytest.mark.anyio
async def test_update_session_allows_exact_version_change_for_preset_sessions() -> None:
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
    agents_binding_mock = AsyncMock(return_value={"enabled": True, "subagents": []})
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
    assert updated.agents_binding == {"enabled": True, "subagents": []}
    agents_binding_mock.assert_awaited_once_with(new_version_id)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(agent_session)


@pytest.mark.anyio
async def test_update_session_clears_selected_version_to_follow_current() -> None:
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
        agents_binding={"enabled": True, "subagents": []},
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
    validate_mock = AsyncMock(return_value=new_version_id)
    agents_binding_mock = AsyncMock(return_value={"enabled": True, "subagents": []})
    service._validate_preset_version_for_assignment = validate_mock
    service._resolve_agents_binding_for_preset_version_id = agents_binding_mock

    updated = await service.update_session(
        agent_session,
        params=AgentSessionUpdate(
            agent_preset_id=mismatched_preset_id,
            agent_preset_version_id=new_version_id,
        ),
    )

    validate_mock.assert_awaited_once_with(
        entity_type=AgentSessionEntity.AGENT_PRESET,
        entity_id=preset_id,
        agent_preset_id=mismatched_preset_id,
        agent_preset_version_id=new_version_id,
    )
    assert updated.agent_preset_id == preset_id
    assert updated.agent_preset_version_id == new_version_id
    assert updated.agents_binding == {"enabled": True, "subagents": []}
    agents_binding_mock.assert_awaited_once_with(new_version_id)
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


@pytest.mark.anyio
async def test_forked_preset_config_carries_resolved_refs() -> None:
    """Invariant: preset-backed forked turns keep one-row-per-turn provenance.

    The forked branch rebuilds a stripped config (no tools/skills/subagents)
    from the parent's preset resolution. It must carry the resolution's
    ``resolved_refs`` through — the value-only snapshot records what
    resolution saw, and without it the provenance staging guard drops the
    row for every forked turn.
    """
    service, _session, role = _build_service()
    workspace_id = role.workspace_id
    assert workspace_id is not None
    parent_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=workspace_id,
        entity_type=AgentSessionEntity.WORKFLOW.value,
        entity_id=uuid.uuid4(),
        parent_session_id=parent_id,
    )
    parent_session = SimpleNamespace(
        agent_preset_id=uuid.uuid4(),
        agent_preset_version_id=None,
    )
    service.get_session = AsyncMock(return_value=parent_session)  # type: ignore[method-assign]

    refs = ResolvedRefs(
        refs=[
            ResolvedRef(
                resource_kind="preset",
                slug="parent-preset",
                resource_id=uuid.uuid4(),
                resolved_version_id=uuid.uuid4(),
                status="ok",
            )
        ]
    )
    preset_config = AgentConfig(
        model_name="test-model",
        model_provider="test-provider",
        instructions="preset instructions",
        actions=["core.workflow.get_workflow"],
        resolved_refs=refs,
    )

    @contextlib.asynccontextmanager
    async def _fake_with_preset_config(**_kwargs: Any):
        yield preset_config

    with patch(
        "tracecat.agent.session.service.AgentManagementService"
    ) as agent_svc_cls:
        agent_svc_cls.return_value = SimpleNamespace(
            with_preset_config=_fake_with_preset_config
        )
        async with service._build_agent_config(agent_session) as resolved:
            assert resolved.actions == []
            assert resolved.resolved_refs == refs


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
