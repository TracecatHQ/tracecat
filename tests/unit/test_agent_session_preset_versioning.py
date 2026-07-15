from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.preset.resolver import (
    ResolvedAgentsRuntimeConfig,
    ResolvedSubagentConfig,
    resolve_agents_config,
)
from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetUpdate
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.session.schemas import AgentSessionCreate, AgentSessionUpdate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    ResolvedAgentsConfig,
    ResolvedAttachedSubagentRef,
)
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import BasicChatRequest
from tracecat.chat.tools import WORKSPACE_CHAT_DEFAULT_TOOLS, get_default_tools
from tracecat.db.models import (
    AgentPreset,
    AgentSession,
    AgentSessionHistory,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
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


def _agent_preset_create(
    *,
    name: str,
    slug: str,
    instructions: str,
    agents: AgentSubagentsConfig | None = None,
) -> AgentPresetCreate:
    return AgentPresetCreate(
        name=name,
        slug=slug,
        description=f"{name} description",
        instructions=instructions,
        model_name="gpt-4o-mini",
        model_provider="openai",
        base_url=None,
        output_type=None,
        actions=None,
        namespaces=None,
        tool_approvals=None,
        mcp_integrations=None,
        agents=agents if agents is not None else AgentSubagentsConfig(),
        retries=3,
        enable_thinking=True,
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
@pytest.mark.parametrize(
    "mode",
    ["derived", "dispatch-deferred", "provided", "internal"],
)
async def test_create_session_binding_permutations(mode: str) -> None:
    """Creation stores, derives, or defers the binding exactly as requested."""

    service, session, _role = _build_service()
    preset_id = None if mode == "internal" else uuid.uuid4()
    selected_version_id = None if mode == "internal" else uuid.uuid4()
    resolved_binding = {"enabled": True, "subagents": []}
    supplied_binding = ResolvedAgentsConfig.model_validate(
        {
            "enabled": True,
            "subagents": (
                [
                    {
                        "preset": "child",
                        "preset_version": 1,
                        "preset_id": uuid.uuid4(),
                        "preset_version_id": uuid.uuid4(),
                    }
                ]
                if mode == "provided"
                else []
            ),
        }
    )
    validate_mock = AsyncMock(return_value=selected_version_id)
    agents_binding_mock = AsyncMock(return_value=resolved_binding)
    service._validate_preset_version_for_assignment = validate_mock
    service._resolve_agents_binding_for_preset_version_id = agents_binding_mock

    params = AgentSessionCreate(
        title="Chat",
        entity_type=AgentSessionEntity.CASE,
        entity_id=uuid.uuid4(),
        agent_preset_id=preset_id,
        agent_preset_version_id=selected_version_id,
    )

    created = await service.create_session(
        params,
        agents_binding=(supplied_binding if mode in {"provided", "internal"} else None),
        resolve_agents_binding=mode != "dispatch-deferred",
    )

    validate_mock.assert_awaited_once_with(
        entity_type=AgentSessionEntity.CASE,
        entity_id=created.entity_id,
        agent_preset_id=preset_id,
        agent_preset_version_id=selected_version_id,
    )
    assert created.agent_preset_id == preset_id
    assert created.agent_preset_version_id == selected_version_id
    expected_binding = {
        "derived": resolved_binding,
        "dispatch-deferred": None,
        "provided": supplied_binding.model_dump(mode="json"),
        "internal": supplied_binding.model_dump(mode="json"),
    }[mode]
    assert created.agents_binding == expected_binding
    if mode == "derived":
        agents_binding_mock.assert_awaited_once_with(selected_version_id)
    else:
        agents_binding_mock.assert_not_awaited()
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(created)


@pytest.mark.anyio
@pytest.mark.parametrize("remove_preset", [False, True], ids=["replace", "remove"])
async def test_update_session_resets_binding_when_preset_changes(
    remove_preset: bool,
) -> None:
    service, session, role = _build_service()
    old_preset_id = uuid.uuid4()
    new_preset_id = None if remove_preset else uuid.uuid4()
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

    if remove_preset:
        validate_mock.assert_not_awaited()
        agents_binding_mock.assert_not_awaited()
    else:
        validate_mock.assert_awaited_once_with(
            entity_type=AgentSessionEntity.CASE,
            entity_id=agent_session.entity_id,
            agent_preset_id=new_preset_id,
            agent_preset_version_id=None,
        )
        agents_binding_mock.assert_awaited_once_with(None)
    assert updated.agent_preset_id == new_preset_id
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
@pytest.mark.parametrize(
    "mode",
    ["exact-version", "follow-current", "ignore-mismatched-preset"],
)
async def test_update_preset_session_version_permutations(mode: str) -> None:
    service, session, role = _build_service()
    preset_id = uuid.uuid4()
    new_version_id = None if mode == "follow-current" else uuid.uuid4()
    requested_preset_id = uuid.uuid4() if mode == "ignore-mismatched-preset" else None
    agent_session = AgentSession(
        workspace_id=role.workspace_id,
        title="Chat",
        created_by=uuid.uuid4(),
        entity_type="agent_preset",
        entity_id=preset_id,
        agent_preset_id=None if mode == "exact-version" else preset_id,
        agent_preset_version_id=uuid.uuid4(),
        agents_binding={"enabled": True, "subagents": []},
    )
    validate_mock = AsyncMock(return_value=new_version_id)
    resolved_binding = (
        None if new_version_id is None else {"enabled": True, "subagents": []}
    )
    agents_binding_mock = AsyncMock(return_value=resolved_binding)
    service._validate_preset_version_for_assignment = validate_mock
    service._resolve_agents_binding_for_preset_version_id = agents_binding_mock

    params = (
        AgentSessionUpdate(
            agent_preset_id=requested_preset_id,
            agent_preset_version_id=new_version_id,
        )
        if mode == "ignore-mismatched-preset"
        else AgentSessionUpdate(agent_preset_version_id=new_version_id)
    )
    updated = await service.update_session(agent_session, params=params)

    if mode == "follow-current":
        validate_mock.assert_not_awaited()
    else:
        validate_mock.assert_awaited_once_with(
            entity_type=AgentSessionEntity.AGENT_PRESET,
            entity_id=preset_id,
            agent_preset_id=requested_preset_id,
            agent_preset_version_id=new_version_id,
        )
    assert updated.agent_preset_id == preset_id
    assert updated.agent_preset_version_id == new_version_id
    assert updated.agents_binding == resolved_binding
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
async def test_workspace_chat_scope_filters_subagent_actions() -> None:
    service = _restricted_service()
    workspace_id = service.role.workspace_id
    assert workspace_id is not None
    child_id = uuid.uuid4()
    child_version_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=workspace_id,
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=workspace_id,
        agent_preset_id=uuid.uuid4(),
    )
    runtime_config = ResolvedAgentsRuntimeConfig(
        enabled=True,
        subagents=[
            ResolvedSubagentConfig(
                binding=ResolvedAttachedSubagentRef(
                    preset="child",
                    preset_id=child_id,
                    preset_version_id=child_version_id,
                ),
                description="Child",
                prompt="Help",
                config=AgentConfigPayload(
                    model_name="test-model",
                    model_provider="test-provider",
                    retries=3,
                    actions=[
                        "core.workflow.get_workflow",
                        "core.workflow.edit_workflow",
                    ],
                ),
            )
        ],
    )
    resolved = SimpleNamespace(to_runtime_config=lambda: runtime_config)

    with patch(
        "tracecat.agent.session.service.resolve_agents_config",
        AsyncMock(return_value=resolved),
    ):
        result = await service._resolve_dispatch_agents_config(
            agent_session=agent_session,
            config=AgentConfig(
                model_name="test-model",
                model_provider="test-provider",
                agents=AgentSubagentsConfig(
                    enabled=True,
                    subagents=[
                        ResolvedAttachedSubagentRef(
                            preset="child",
                            preset_id=child_id,
                            preset_version_id=child_version_id,
                        )
                    ],
                ),
            ),
        )

    assert result.subagents[0].config.actions == ["core.workflow.get_workflow"]


@pytest.mark.anyio
async def test_forked_preset_config_strips_runtime_capabilities() -> None:
    """Preset-backed forked turns retain instructions but cannot use tools."""
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

    preset_config = AgentConfig(
        model_name="test-model",
        model_provider="test-provider",
        instructions="preset instructions",
        actions=["core.workflow.get_workflow"],
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
            assert resolved.instructions is not None
            assert resolved.instructions.endswith("preset instructions")


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


@pytest.mark.anyio
async def test_run_turn_dispatch_passes_one_full_resolved_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch-resolved turns pass one resolved tree in workflow arguments."""
    service, _session, role = _build_service()
    workspace_id = role.workspace_id
    assert workspace_id is not None
    session_id = uuid.uuid4()
    preset_id = uuid.uuid4()
    agent_session = AgentSession(
        workspace_id=workspace_id,
        title="Dispatch chat",
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=preset_id,
        agent_preset_id=preset_id,
    )
    agent_session.id = session_id
    config = AgentConfig(
        model_name="gpt-4o-mini",
        model_provider="openai",
    )
    resolved_agents = ResolvedAgentsRuntimeConfig(enabled=True)

    @contextlib.asynccontextmanager
    async def _fake_build_agent_config(_agent_session: AgentSession):
        yield config

    client = SimpleNamespace(start_workflow=AsyncMock())
    service.validate_turn_request = AsyncMock(return_value=agent_session)  # type: ignore[method-assign]
    service.auto_title_session_on_first_prompt = AsyncMock()  # type: ignore[method-assign]
    service._build_agent_config = _fake_build_agent_config  # type: ignore[method-assign]
    service._resolve_dispatch_agents_config = AsyncMock(return_value=resolved_agents)  # type: ignore[method-assign]
    monkeypatch.setattr(
        "tracecat.agent.session.service.get_temporal_client",
        AsyncMock(return_value=client),
    )

    response = await service.run_turn(
        session_id,
        BasicChatRequest(message="hello"),
        active_stream_id=uuid.uuid4(),
    )

    assert response is not None
    assert agent_session.agents_binding is None
    workflow_args = client.start_workflow.await_args.args[1]
    assert workflow_args.agent_args.config == config
    assert workflow_args.agent_args.resolved_agents_config == resolved_agents


@pytest.mark.anyio
async def test_new_dispatch_turn_follows_child_head_after_session_history(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """A new run resolves current heads even when the chat has SDK history."""
    workspace_id = svc_role.workspace_id
    assert workspace_id is not None
    preset_service = AgentPresetService(session=session, role=svc_role)
    child = await preset_service.create_preset(
        _agent_preset_create(
            name="Resume child",
            slug="resume-child",
            instructions="Stored child instructions",
        )
    )
    parent = await preset_service.create_preset(
        _agent_preset_create(
            name="Resume parent",
            slug="resume-parent",
            instructions="Parent instructions",
            agents=AgentSubagentsConfig.model_validate(
                {
                    "enabled": True,
                    "subagents": [{"preset": child.slug}],
                }
            ),
        )
    )
    stored_config = await preset_service.resolve_agent_preset_config(
        preset_id=parent.id
    )
    stored_resolution = await resolve_agents_config(
        preset_service,
        agents=stored_config.agents,
        parent_preset_id=parent.id,
        parent_slug=parent.slug,
    )
    stored_binding = stored_resolution.to_agents_binding()
    assert len(stored_binding.subagents) == 1
    stored_child_version_id = stored_binding.subagents[0].preset_version_id

    agent_session = AgentSession(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        title="Resumed dispatch chat",
        entity_type=AgentSessionEntity.AGENT_PRESET.value,
        entity_id=parent.id,
        agent_preset_id=parent.id,
        sdk_session_id="sdk-session-resume",
        agents_binding=stored_binding.model_dump(mode="json"),
    )
    session.add(agent_session)
    session.add(
        AgentSessionHistory(
            workspace_id=workspace_id,
            session_id=agent_session.id,
            kind=MessageKind.CHAT_MESSAGE.value,
            content={"type": "user", "message": {"content": "previous turn"}},
        )
    )
    await session.commit()

    await preset_service.update_preset(
        child,
        AgentPresetUpdate(instructions="Advanced child instructions"),
    )
    child_v2 = await preset_service.get_current_version_for_preset(child)
    assert child_v2.id != stored_child_version_id
    service = AgentSessionService(session=session, role=svc_role)
    fake_client = SimpleNamespace(start_workflow=AsyncMock(return_value=None))
    with (
        patch(
            "tracecat.agent.service.AgentManagementService.get_workspace_runtime_provider_credentials",
            AsyncMock(return_value={"OPENAI_API_KEY": "test-key"}),
        ),
        patch(
            "tracecat.agent.service.AgentManagementService.get_runtime_provider_credentials",
            AsyncMock(return_value=None),
        ),
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            AsyncMock(return_value=fake_client),
        ),
    ):
        response = await service.run_turn(
            agent_session.id,
            BasicChatRequest(message="continue"),
            active_stream_id=uuid.uuid4(),
        )

    assert response is not None
    workflow_args = fake_client.start_workflow.await_args.args[1]
    resolved_agents = workflow_args.agent_args.resolved_agents_config
    assert resolved_agents is not None
    assert resolved_agents.subagents[0].binding.preset_version_id == child_v2.id
    assert resolved_agents.to_agents_binding() != stored_binding
    await session.refresh(agent_session)
    assert (
        ResolvedAgentsConfig.model_validate(agent_session.agents_binding)
        == stored_binding
    )


@pytest.mark.anyio
async def test_preset_builder_prompt_reads_current_version(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    preset_service = AgentPresetService(session=session, role=svc_role)
    with patch.object(preset_service, "_validate_actions", AsyncMock()):
        preset = await preset_service.create_preset(
            AgentPresetCreate(
                name="Prompt preset",
                slug="prompt-preset",
                description="Prompt description",
                instructions="Investigate the alert carefully.",
                model_name="gpt-5.5",
                model_provider="openai",
                actions=["core.http_request"],
                namespaces=["core"],
                tool_approvals={"core.http_request": False},
            )
        )
    agent_session = AgentSession(
        workspace_id=svc_role.workspace_id,
        title="Builder",
        entity_type=AgentSessionEntity.AGENT_PRESET_BUILDER.value,
        entity_id=preset.id,
    )

    prompt = await AgentSessionService(
        session=session, role=svc_role
    )._entity_to_prompt(agent_session)

    assert "Investigate the alert carefully." in prompt
    assert "Allowed tools: core.http_request" in prompt
    assert "Namespace limits: core" in prompt
    assert "core.http_request: requires manual approval" in prompt


@pytest.mark.anyio
async def test_preset_builder_prompt_rejects_unpublished_head(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    preset = AgentPreset(
        workspace_id=svc_role.workspace_id,
        name="Unpublished prompt preset",
        slug="unpublished-prompt-preset",
        model_name="gpt-4o-mini",
        model_provider="openai",
    )
    session.add(preset)
    await session.commit()
    agent_session = AgentSession(
        workspace_id=svc_role.workspace_id,
        title="Builder",
        entity_type=AgentSessionEntity.AGENT_PRESET_BUILDER.value,
        entity_id=preset.id,
    )

    with pytest.raises(TracecatNotFoundError, match="no current published version"):
        await AgentSessionService(session=session, role=svc_role)._entity_to_prompt(
            agent_session
        )
