from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from tracecat.agent.preset.activities import (
    ResolveAgentPresetDispatchActivityInput,
    ResolveAgentPresetVersionRefActivityInput,
    ResolveAgentsConfigActivityInput,
    resolve_agent_preset_dispatch_activity,
    resolve_agent_preset_version_ref_activity,
    resolve_agents_config_activity,
    resolve_custom_model_provider_config_activity,
)
from tracecat.agent.preset.resolver import (
    ResolvedAgentsRuntimeConfig,
    ResolvedSubagentConfig,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    ResolvedAttachedSubagentRef,
)
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


@pytest.fixture(scope="session")
def minio_server() -> Iterator[None]:
    yield


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    yield


@pytest.mark.anyio
async def test_resolve_agent_preset_version_ref_activity_ignores_legacy_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = SimpleNamespace(id=uuid.uuid4(), preset_id=uuid.uuid4())
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version)
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agent_preset_version_ref_activity(
        ResolveAgentPresetVersionRefActivityInput.model_validate(
            {
                "role": role,
                "preset_slug": "triage-agent",
                "preset_version": 3,
            }
        )
    )

    service.resolve_agent_preset_version.assert_awaited_once_with(slug="triage-agent")
    assert result.preset_id == version.preset_id
    assert result.preset_version_id == version.id


@pytest.mark.anyio
async def test_resolve_agent_preset_dispatch_activity_returns_complete_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    root_id = uuid.uuid4()
    root_version_id = uuid.uuid4()
    child_id = uuid.uuid4()
    child_version_id = uuid.uuid4()
    root_version = SimpleNamespace(id=root_version_id, preset_id=root_id)
    child_version = SimpleNamespace(
        id=child_version_id,
        preset_id=child_id,
        version=2,
        tool_approvals={},
    )
    root_config = AgentConfig(
        model_name="gpt-4o-mini",
        model_provider="openai",
        instructions="Root instructions",
        actions=["core.workflow.get_workflow"],
        agents=AgentSubagentsConfig.model_validate(
            {"enabled": True, "subagents": [{"preset": "analyst"}]}
        ),
    )
    child_config = AgentConfig(
        model_name="gpt-4o-mini",
        model_provider="openai",
        instructions="Child instructions",
    )
    presets = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=root_version),
        resolve_agent_preset_version_for_subagent_ref=AsyncMock(
            return_value=child_version
        ),
        _get_version_agents_config=AsyncMock(return_value=AgentSubagentsConfig()),
        get_preset=AsyncMock(
            return_value=SimpleNamespace(description="Analyze the evidence")
        ),
        resolve_agent_preset_config=AsyncMock(return_value=child_config),
    )
    management_service = SimpleNamespace(
        presets=presets,
        with_preset_config=lambda **_: _AsyncContext(root_config),
    )
    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentManagementService.with_session",
        lambda **_: _AsyncContext(management_service),
    )

    result = await resolve_agent_preset_dispatch_activity(
        ResolveAgentPresetDispatchActivityInput(
            role=role,
            preset_slug="root-agent",
            actions=["core.http_request"],
            instructions="Extra instructions",
        )
    )

    assert result.preset_id == root_id
    assert result.preset_version_id == root_version_id
    assert result.config.actions == ["core.http_request"]
    assert result.config.instructions == "Root instructions\nExtra instructions"
    assert result.resolved_agents_config.enabled is True
    assert len(result.resolved_agents_config.subagents) == 1
    subagent = result.resolved_agents_config.subagents[0]
    assert subagent.binding.preset_id == child_id
    assert subagent.binding.preset_version_id == child_version_id
    assert subagent.config.instructions == "Child instructions"
    presets.resolve_agent_preset_version_for_subagent_ref.assert_awaited_once_with(
        slug="analyst"
    )


def test_resolve_agents_config_input_defaults_preserve_resolved_versions() -> None:
    """Workflow histories recorded before the flag existed must deserialize,
    defaulting to fresh current-head resolution."""
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    payload = ResolveAgentsConfigActivityInput(role=role).model_dump(mode="json")
    payload.pop("preserve_resolved_versions")
    parsed = ResolveAgentsConfigActivityInput.model_validate(payload)
    assert parsed.preserve_resolved_versions is False


def test_resolve_agents_config_result_derives_session_binding() -> None:
    binding = ResolvedAttachedSubagentRef(
        preset="analyst",
        preset_version=3,
        name=None,
        description=None,
        max_turns=5,
        preset_id=uuid.uuid4(),
        preset_version_id=uuid.uuid4(),
    )
    result = ResolvedAgentsRuntimeConfig(
        enabled=True,
        subagents=[
            ResolvedSubagentConfig(
                binding=binding,
                description="Runtime fallback description",
                prompt="Subagent prompt",
                config=AgentConfigPayload(
                    model_name="gpt-4o-mini",
                    model_provider="openai",
                    retries=3,
                ),
            )
        ],
    )

    assert result.subagents[0].alias == "analyst"
    assert result.subagents[0].max_turns == 5
    agents_binding = result.to_agents_binding()
    assert agents_binding.enabled is True
    assert agents_binding.subagents == [binding]


@pytest.mark.anyio
async def test_resolve_agents_activity_returns_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The activity returns the resolved runtime subagent tree."""
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    service = SimpleNamespace(role=role)
    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agents_config_activity(
        ResolveAgentsConfigActivityInput(
            role=role,
            agents=AgentSubagentsConfig(),
        )
    )

    assert result == ResolvedAgentsRuntimeConfig()


@pytest.mark.anyio
async def test_resolve_preset_subagent_configs_uses_preset_id_ref() -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    service = AgentPresetService(cast(Any, SimpleNamespace()), role)
    preset_id = uuid.uuid4()
    preset_version_id = uuid.uuid4()
    version = SimpleNamespace(
        id=preset_version_id,
        preset_id=preset_id,
        version=8,
        agents={"enabled": False},
        tool_approvals={},
    )
    service.resolve_agent_preset_version = AsyncMock(return_value=version)
    service.resolve_agent_preset_version_for_subagent_ref = AsyncMock(
        return_value=version
    )
    service._lock_active_subagent_presets = AsyncMock()  # type: ignore[method-assign]
    # The edge-authoritative ban check hits the DB; stub it for the double.
    service._get_version_agents_config = AsyncMock(  # type: ignore[method-assign]
        return_value=AgentSubagentsConfig(enabled=False)
    )
    service.get_preset = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(slug="old-analyst-slug", description=None)
    )

    result = await service._resolve_preset_subagent_configs(
        AgentSubagentsConfig(
            enabled=True,
            subagents=[
                ResolvedAttachedSubagentRef(
                    preset="old-analyst-slug",
                    preset_version=2,
                    name="analyst",
                    description=None,
                    max_turns=3,
                    preset_id=preset_id,
                    preset_version_id=preset_version_id,
                )
            ],
        ),
        parent_preset_id=uuid.uuid4(),
        parent_slug="parent",
    )

    service.resolve_agent_preset_version_for_subagent_ref.assert_awaited_once_with(
        preset_id=preset_id,
    )
    service.resolve_agent_preset_version.assert_not_awaited()
    assert result.subagents[0].preset_version_id == preset_version_id
    assert result.subagents[0].preset_version == 8


@pytest.mark.anyio
async def test_resolve_agents_config_resolves_persisted_ref_by_preset_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preset_id = uuid.uuid4()
    preset_version_id = uuid.uuid4()
    version = SimpleNamespace(
        id=preset_version_id,
        preset_id=preset_id,
        version=4,
        agents={"enabled": False},
        tool_approvals={},
    )
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version),
        resolve_agent_preset_version_for_subagent_ref=AsyncMock(return_value=version),
        _get_version_agents_config=AsyncMock(
            return_value=AgentSubagentsConfig(enabled=False)
        ),
        get_preset=AsyncMock(
            return_value=SimpleNamespace(
                slug="child-preset", description="Child preset"
            )
        ),
        resolve_agent_preset_config=AsyncMock(
            return_value=AgentConfig(
                model_name="gpt-4o-mini",
                model_provider="openai",
                retries=3,
            )
        ),
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agents_config_activity(
        ResolveAgentsConfigActivityInput(
            role=role,
            agents=AgentSubagentsConfig(
                enabled=True,
                subagents=[
                    ResolvedAttachedSubagentRef(
                        preset="old-analyst-slug",
                        preset_version=2,
                        name="analyst",
                        description=None,
                        max_turns=None,
                        preset_id=preset_id,
                        preset_version_id=preset_version_id,
                    )
                ],
            ),
        )
    )

    service.resolve_agent_preset_version_for_subagent_ref.assert_awaited_once_with(
        preset_id=preset_id,
    )
    service.resolve_agent_preset_version.assert_not_awaited()
    assert result.subagents[0].binding.preset_version_id == preset_version_id
    assert result.subagents[0].binding.preset_version == 4


@pytest.mark.anyio
async def test_resolve_agents_config_rejects_subagent_with_tool_approvals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = SimpleNamespace(
        id=uuid.uuid4(),
        preset_id=uuid.uuid4(),
        version=1,
        agents={"enabled": False},
        tool_approvals={"core.http_request": True},
    )
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version),
        resolve_agent_preset_version_for_subagent_ref=AsyncMock(return_value=version),
        _get_version_agents_config=AsyncMock(
            return_value=AgentSubagentsConfig(enabled=False)
        ),
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    with pytest.raises(
        TracecatValidationError,
        match=(
            "Subagent preset 'approval-child' uses manual approvals, "
            "which are not supported for subagents yet."
        ),
    ):
        await resolve_agents_config_activity(
            ResolveAgentsConfigActivityInput(
                role=role,
                agents=AgentSubagentsConfig.model_validate(
                    {
                        "enabled": True,
                        "subagents": [{"preset": "approval-child"}],
                    }
                ),
            )
        )


@pytest.mark.anyio
async def test_resolve_agents_config_rejects_invalid_fallback_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(SimpleNamespace()),
    )

    with pytest.raises(
        TracecatValidationError,
        match="Invalid subagent alias 'Bad Alias'",
    ):
        await resolve_agents_config_activity(
            ResolveAgentsConfigActivityInput(
                role=role,
                agents=AgentSubagentsConfig.model_validate(
                    {
                        "enabled": True,
                        "subagents": [{"preset": "Bad Alias"}],
                    }
                ),
            )
        )


@pytest.mark.anyio
async def test_resolve_custom_model_provider_config_activity_returns_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SimpleNamespace(
        get_workspace_provider_credentials=AsyncMock(
            return_value={
                "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://customer.example",
                "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "provider/custom-model",
                "CUSTOM_MODEL_PROVIDER_PASSTHROUGH": "true",
            }
        )
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentManagementService.with_session",
        lambda *_args, **_kwargs: _AsyncContext(service),
    )

    result = await resolve_custom_model_provider_config_activity(role)

    service.get_workspace_provider_credentials.assert_awaited_once_with(
        "custom-model-provider",
    )
    assert result.base_url == "https://customer.example"
    assert result.model_name == "provider/custom-model"
    assert result.passthrough is True
