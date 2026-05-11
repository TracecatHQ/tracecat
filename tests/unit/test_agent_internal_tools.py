from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from tracecat_registry import RegistryOAuthSecret

from tracecat.agent.mcp import internal_tools
from tracecat.agent.preset.schemas import AgentPresetRead
from tracecat.agent.tokens import InternalToolContext, MCPTokenClaims
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.schemas import ProviderKey


class _AsyncContext:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _build_claims(preset_id: uuid.UUID) -> MCPTokenClaims:
    return MCPTokenClaims(
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        allowed_actions=[],
        allowed_internal_tools=["internal.builder.update_preset"],
        internal_tool_context=InternalToolContext(
            preset_id=preset_id,
            entity_type="agent_preset_builder",
        ),
    )


def _build_preset_read(preset: dict[str, object]) -> AgentPresetRead:
    data = dict(preset)
    data.setdefault("skills", [])
    return AgentPresetRead.model_validate(data)


def test_evaluate_configuration_prefers_workspace_secret_even_when_empty():
    requirements = [
        {
            "name": "slack",
            "required_keys": ["SLACK_BOT_TOKEN"],
            "optional": False,
        }
    ]
    workspace_inventory = {"slack": set()}
    org_inventory = {"slack": {"SLACK_BOT_TOKEN"}}

    configured, missing = internal_tools._evaluate_configuration(
        requirements,
        workspace_inventory,
        org_inventory,
        set(),
    )

    assert configured is False
    assert missing == ["missing key: slack.SLACK_BOT_TOKEN"]


def test_evaluate_configuration_accepts_configured_oauth_integration():
    requirements = [
        {
            "type": "oauth",
            "provider_id": "google_docs",
            "grant_type": "client_credentials",
            "optional": False,
        }
    ]

    configured, missing = internal_tools._evaluate_configuration(
        requirements,
        {},
        {},
        {
            ProviderKey(
                id="google_docs",
                grant_type=OAuthGrantType.CLIENT_CREDENTIALS,
            )
        },
    )

    assert configured is True
    assert missing == []


@pytest.mark.anyio
async def test_list_available_tools_includes_configuration_fields(monkeypatch):
    preset_id = uuid.uuid4()
    claims = _build_claims(preset_id)

    preset_service = SimpleNamespace(
        get_preset=lambda _preset_id: None,
    )

    async def _get_preset(_preset_id):
        return SimpleNamespace(actions=["tools.alpha"])

    preset_service.get_preset = _get_preset

    async def _search_actions(_query):
        return [
            (
                SimpleNamespace(
                    namespace="tools",
                    name="alpha",
                    description="Alpha tool",
                ),
                "origin",
            ),
            (
                SimpleNamespace(
                    namespace="tools",
                    name="beta",
                    description="Beta tool",
                ),
                "origin",
            ),
        ]

    registry_service = SimpleNamespace(search_actions_from_index=_search_actions)

    async def _config(*_args, **_kwargs):
        action_name = _args[1]
        if action_name == "tools.alpha":
            return True, []
        return False, ["missing secret: beta"]

    async def _secret_inventory(_role):
        return {}, {}

    async def _oauth_inventory(_role):
        return set()

    monkeypatch.setattr(
        "tracecat.agent.preset.service.AgentPresetService.with_session",
        lambda role: _AsyncContext(preset_service),
    )
    monkeypatch.setattr(
        "tracecat.agent.mcp.internal_tools.RegistryActionsService.with_session",
        lambda role: _AsyncContext(registry_service),
    )
    monkeypatch.setattr(
        internal_tools,
        "_load_secret_inventory",
        _secret_inventory,
    )
    monkeypatch.setattr(
        internal_tools,
        "_load_oauth_inventory",
        _oauth_inventory,
    )
    monkeypatch.setattr(internal_tools, "_get_action_configuration", _config)

    result = await internal_tools.list_available_tools({"query": "tool"}, claims)
    assert len(result["tools"]) == 2
    alpha = next(t for t in result["tools"] if t["action_id"] == "tools.alpha")
    beta = next(t for t in result["tools"] if t["action_id"] == "tools.beta")
    assert alpha["configured"] is True
    assert alpha["already_in_preset"] is True
    assert beta["configured"] is False
    assert beta["missing_requirements"] == ["missing secret: beta"]


@pytest.mark.anyio
async def test_update_preset_blocks_unconfigured_tool_add(monkeypatch):
    preset_id = uuid.uuid4()
    claims = _build_claims(preset_id)

    async def _get_preset(_preset_id):
        return SimpleNamespace(actions=["tools.alpha"])

    async def _update_preset(_preset, _params):
        return _preset

    preset_service = SimpleNamespace(
        get_preset=_get_preset,
        update_preset=_update_preset,
    )
    registry_service = SimpleNamespace()

    async def _config(*_args, **_kwargs):
        return False, ["missing key: slack.SLACK_BOT_TOKEN"]

    async def _secret_inventory(_role):
        return {}, {}

    async def _oauth_inventory(_role):
        return set()

    monkeypatch.setattr(
        "tracecat.agent.preset.service.AgentPresetService.with_session",
        lambda role: _AsyncContext(preset_service),
    )
    monkeypatch.setattr(
        "tracecat.agent.mcp.internal_tools.RegistryActionsService.with_session",
        lambda role: _AsyncContext(registry_service),
    )
    monkeypatch.setattr(
        internal_tools,
        "_load_secret_inventory",
        _secret_inventory,
    )
    monkeypatch.setattr(
        internal_tools,
        "_load_oauth_inventory",
        _oauth_inventory,
    )
    monkeypatch.setattr(internal_tools, "_get_action_configuration", _config)

    with pytest.raises(
        internal_tools.InternalToolError, match="Cannot add unconfigured"
    ):
        await internal_tools.update_preset(
            {"actions": ["tools.alpha", "tools.slack.post_message"]},
            claims,
        )


@pytest.mark.anyio
async def test_update_preset_allows_configured_oauth_tool_add(monkeypatch):
    preset_id = uuid.uuid4()
    claims = _build_claims(preset_id)

    existing_preset = SimpleNamespace(actions=["tools.alpha"])
    updated_preset = {
        "id": preset_id,
        "workspace_id": claims.workspace_id,
        "name": "Builder Preset",
        "slug": "builder-preset",
        "description": None,
        "current_version_id": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "instructions": None,
        "model_name": "gpt-5.4",
        "model_provider": "openai",
        "base_url": None,
        "output_type": None,
        "actions": ["tools.alpha", "tools.google_docs.create_document"],
        "namespaces": None,
        "tool_approvals": None,
        "mcp_integrations": None,
        "retries": 3,
        "enable_internet_access": False,
    }

    async def _get_preset(_preset_id):
        return existing_preset

    async def _update_preset(_preset, _params):
        return updated_preset

    async def _build_preset(_preset):
        return _build_preset_read(updated_preset)

    async def _get_action_from_index(_action_name):
        return SimpleNamespace(manifest=object())

    registry_service = SimpleNamespace(
        get_action_from_index=_get_action_from_index,
        aggregate_secrets_from_manifest=lambda _manifest, _action_name: [
            RegistryOAuthSecret(
                provider_id="google_docs",
                grant_type="client_credentials",
            )
        ],
    )
    preset_service = SimpleNamespace(
        get_preset=_get_preset,
        update_preset=_update_preset,
        build_preset_read=_build_preset,
    )

    async def _secret_inventory(_role):
        return {}, {}

    async def _oauth_inventory(_role):
        return {
            ProviderKey(
                id="google_docs",
                grant_type=OAuthGrantType.CLIENT_CREDENTIALS,
            )
        }

    monkeypatch.setattr(
        "tracecat.agent.preset.service.AgentPresetService.with_session",
        lambda role: _AsyncContext(preset_service),
    )
    monkeypatch.setattr(
        "tracecat.agent.mcp.internal_tools.RegistryActionsService.with_session",
        lambda role: _AsyncContext(registry_service),
    )
    monkeypatch.setattr(
        internal_tools,
        "_load_secret_inventory",
        _secret_inventory,
    )
    monkeypatch.setattr(
        internal_tools,
        "_load_oauth_inventory",
        _oauth_inventory,
    )

    result = await internal_tools.update_preset(
        {"actions": ["tools.alpha", "tools.google_docs.create_document"]},
        claims,
    )

    assert result["actions"] == ["tools.alpha", "tools.google_docs.create_document"]
