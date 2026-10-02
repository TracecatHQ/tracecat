"""Workspace limits remain authoritative over stale or crafted chat choices."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.session.schemas import WorkspaceChatOverrides
from tracecat.agent.session.service import AgentSessionService
from tracecat.auth.types import Role
from tracecat.chat.tools import select_workspace_chat_capabilities
from tracecat.db.models import AgentSession
from tracecat.workspaces.schemas import (
    ChatCapabilitySelection,
    WorkspaceChatSettings,
    WorkspaceSettingsRead,
)


@pytest.mark.parametrize(
    ("mode", "selected", "override", "expected"),
    [
        ("all", [], None, ["existing", "new"]),
        ("selected", ["existing"], None, ["existing"]),
        ("selected", [], None, []),
        ("none", ["existing"], ["existing", "new"], []),
        ("all", [], [], []),
        ("all", [], ["existing", "foreign"], ["existing"]),
        ("selected", ["existing", "deleted"], ["new", "deleted"], []),
    ],
)
def test_capability_intersection(mode, selected, override, expected):
    selection = ChatCapabilitySelection(mode=mode, selected=selected)
    assert (
        select_workspace_chat_capabilities(["existing", "new"], selection, override)
        == expected
    )


def test_missing_and_reset_settings_inherit_but_empty_overrides_deny():
    assert WorkspaceSettingsRead.model_validate({}).chat == WorkspaceChatSettings()
    assert (
        WorkspaceSettingsRead.model_validate({"chat": None}).chat
        == WorkspaceChatSettings()
    )
    assert WorkspaceChatOverrides().tools is None
    assert WorkspaceChatOverrides(tools=[]).tools == []
    assert WorkspaceChatOverrides().subagents is None
    assert WorkspaceChatOverrides(subagents=[]).subagents == []
    with pytest.raises(ValidationError):
        WorkspaceChatOverrides(subagents=["invalid"])
    with pytest.raises(ValidationError):
        WorkspaceChatOverrides(mcp_integrations=["invalid"])
    with pytest.raises(ValidationError):
        WorkspaceChatSettings.model_validate({"subagents": {"selected": ["invalid"]}})


@pytest.mark.anyio
@pytest.mark.parametrize("unavailable_mcp_state", ["configured", "reauth_required"])
async def test_each_turn_rechecks_limits_catalog_and_caller_scopes(
    unavailable_mcp_state,
):
    workspace_id = uuid.uuid4()
    allowed_mcp, disconnected_mcp, foreign_mcp = (uuid.uuid4() for _ in range(3))
    eligible, ineligible = (uuid.uuid4() for _ in range(2))
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute", "action:core.test.read:execute"}),
    )
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = {
        "chat": {
            "tools": {
                "mode": "selected",
                "selected": ["core.test.read", "core.test.write"],
            },
            "mcp": {"mode": "all"},
            "subagents": {"mode": "all"},
        }
    }
    service = AgentSessionService(session, role)
    chat = AgentSession(
        workspace_id=workspace_id,
        entity_type="copilot",
        entity_id=workspace_id,
        tools=["core.test.write"],
        mcp_integrations=[str(foreign_mcp)],
        workspace_chat_overrides={
            "tools": ["core.test.read", "core.test.write", "foreign.action"],
            "mcp_integrations": [
                str(allowed_mcp),
                str(disconnected_mcp),
                str(foreign_mcp),
            ],
        },
    )
    presets = [
        SimpleNamespace(
            id=preset_id,
            slug=slug,
            name=slug,
            description=None,
            current_version_subagent_eligibility=SimpleNamespace(eligible=can_attach),
        )
        for preset_id, slug, can_attach in (
            (eligible, "helper", True),
            (ineligible, "nested", False),
        )
    ]
    with (
        patch("tracecat.agent.session.service.RegistryActionsService") as registry,
        patch("tracecat.agent.session.service.IntegrationService") as integrations,
        patch("tracecat.agent.session.service.AgentPresetService") as preset_service,
        patch(
            "tracecat.agent.session.service.filter_configured_actions",
            new_callable=AsyncMock,
        ) as configured,
    ):
        configured.side_effect = lambda actions, **kwargs: actions
        registry.return_value.list_actions_from_index = AsyncMock(
            return_value=[
                (SimpleNamespace(namespace="core.test", name=name), "platform")
                for name in ("read", "write")
            ]
        )
        integrations.return_value.list_mcp_integrations_with_state = AsyncMock(
            return_value=[
                SimpleNamespace(
                    integration=SimpleNamespace(id=allowed_mcp), state="connected"
                ),
                SimpleNamespace(
                    integration=SimpleNamespace(id=disconnected_mcp),
                    state=unavailable_mcp_state,
                ),
            ]
        )
        preset_service.return_value.list_presets = AsyncMock(return_value=[])
        preset_service.return_value.build_preset_list_reads = AsyncMock(
            return_value=presets
        )
        resolver = AsyncMock(
            return_value=[
                {
                    "type": "http",
                    "name": "server",
                    "url": "https://mcp.example.test",
                    "id": str(allowed_mcp),
                }
            ]
        )
        preset_service.return_value.resolve_mcp_integration_refs = resolver
        actions, servers, agents = await service._resolve_workspace_chat_capabilities(
            chat
        )
        assert actions == ["core.test.read"]
        configured.assert_awaited_once_with(
            ["core.test.read"], registry=registry.return_value, role=role
        )
        assert servers == resolver.return_value
        resolver.assert_awaited_once_with([str(allowed_mcp)])
        assert [agent.preset for agent in agents.subagents] == ["helper"]

        # A one-chat choice cannot attach foreign or ineligible saved agents.
        chat.workspace_chat_overrides = {
            "tools": None,
            "mcp_integrations": None,
            "subagents": [str(eligible), str(ineligible), str(uuid.uuid4())],
        }
        _, _, agents = await service._resolve_workspace_chat_capabilities(chat)
        assert [agent.preset for agent in agents.subagents] == ["helper"]
        resolver.reset_mock()

        # The same session loses access immediately on the next turn.
        session.scalar.return_value = {
            "chat": {key: {"mode": "none"} for key in ("tools", "mcp", "subagents")}
        }
        actions, servers, agents = await service._resolve_workspace_chat_capabilities(
            chat
        )
        assert actions == []
        assert servers is None
        assert agents.subagents == []
        resolver.assert_not_awaited()

        # Clearing an override inherits current settings; [] still means none.
        session.scalar.return_value = None
        chat.workspace_chat_overrides = None
        actions, _, agents = await service._resolve_workspace_chat_capabilities(chat)
        assert actions == ["core.test.read"]
        assert [agent.preset for agent in agents.subagents] == ["helper"]
        chat.workspace_chat_overrides = {
            "tools": [],
            "mcp_integrations": [],
            "subagents": [],
        }
        actions, servers, agents = await service._resolve_workspace_chat_capabilities(
            chat
        )
        assert actions == []
        assert servers is None
        assert agents.subagents == []
