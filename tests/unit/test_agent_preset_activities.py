from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.agent.preset.activities import (
    ResolveAgentPresetConfigActivityInput,
    ResolveAgentPresetVersionRefActivityInput,
    ResolveAgentsConfigActivityInput,
    resolve_agent_preset_config_activity,
    resolve_agent_preset_version_ref_activity,
    resolve_agents_config_activity,
    resolve_custom_model_provider_config_activity,
)
from tracecat.agent.preset.resolver import (
    ResolvedAgentsRuntimeConfig,
    ResolvedSubagentConfig,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.subagents import AgentSubagentsConfig, ResolvedAttachedSubagentRef
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.runtime.errors import RuntimeErrorKind, RuntimeErrorOwner
from tracecat.temporal.errors import extract_error_classification


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FailingAsyncContext:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def __aenter__(self) -> None:
        raise self._error

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.fixture(scope="session")
def minio_server() -> Iterator[None]:
    yield


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    yield


@pytest.mark.anyio
async def test_resolve_agent_preset_version_ref_activity_returns_ids(
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
        ResolveAgentPresetVersionRefActivityInput(
            role=role,
            preset_slug="triage-agent",
            preset_version=3,
        )
    )

    service.resolve_agent_preset_version.assert_awaited_once_with(
        slug="triage-agent",
        preset_version=3,
    )
    assert result.preset_id == version.preset_id
    assert result.preset_version_id == version.id


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        TracecatNotFoundError("Agent preset not found"),
        TracecatValidationError("Preset version does not belong to preset"),
    ],
)
async def test_resolve_agent_preset_config_classifies_user_input_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    service = SimpleNamespace(
        with_preset_config=lambda **_: _FailingAsyncContext(error)
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentManagementService.with_session",
        lambda **_: _AsyncContext(service),
    )

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_agent_preset_config_activity(
            ResolveAgentPresetConfigActivityInput(
                role=role,
                preset_slug="missing-preset",
            )
        )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert exc_info.value.non_retryable is True


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
async def test_resolve_preset_subagent_configs_resolves_version_id_ref() -> None:
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
    service._lock_active_subagent_presets = AsyncMock()  # type: ignore[method-assign]

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

    service.resolve_agent_preset_version.assert_awaited_once_with(
        preset_version_id=preset_version_id,
    )
    assert result["subagents"][0]["preset_version_id"] == str(preset_version_id)
    assert result["subagents"][0]["preset_version"] == 8


@pytest.mark.anyio
async def test_resolve_agents_config_resolves_pinned_ref_by_version_id(
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
        get_preset=AsyncMock(return_value=SimpleNamespace(description="Child preset")),
        resolve_agent_preset_config=AsyncMock(
            return_value=AgentConfig(
                model_name="gpt-4o-mini",
                model_provider="openai",
                retries=3,
            )
        ),
        use_latest_resource_versions=AsyncMock(return_value=False),
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

    service.resolve_agent_preset_version.assert_awaited_once_with(
        preset_version_id=preset_version_id,
    )
    service.use_latest_resource_versions.assert_awaited_once()
    assert result.subagents[0].binding.preset_version_id == preset_version_id
    assert result.subagents[0].binding.preset_version == 4


@pytest.mark.anyio
async def test_resolve_agents_config_explicitly_disables_latest_resolution(
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
        get_preset=AsyncMock(return_value=SimpleNamespace(description="Child preset")),
        resolve_agent_preset_config=AsyncMock(
            return_value=AgentConfig(
                model_name="gpt-4o-mini",
                model_provider="openai",
                retries=3,
            )
        ),
        use_latest_resource_versions=AsyncMock(return_value=True),
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
                        preset_id=preset_id,
                        preset_version_id=preset_version_id,
                    )
                ],
            ),
            follow_latest_versions=False,
        )
    )

    service.use_latest_resource_versions.assert_not_awaited()
    service.resolve_agent_preset_version.assert_awaited_once_with(
        preset_version_id=preset_version_id,
    )
    assert result.subagents[0].binding.preset_version_id == preset_version_id


@pytest.mark.anyio
async def test_resolve_agents_config_classifies_missing_subagent_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(
            side_effect=TracecatNotFoundError("Agent preset 'missing-child' not found")
        ),
        use_latest_resource_versions=AsyncMock(return_value=False),
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

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_agents_config_activity(
            ResolveAgentsConfigActivityInput(
                role=role,
                agents=AgentSubagentsConfig.model_validate(
                    {
                        "enabled": True,
                        "subagents": [{"preset": "missing-child"}],
                    }
                ),
            )
        )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert exc_info.value.non_retryable is True


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
        use_latest_resource_versions=AsyncMock(return_value=False),
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

    with pytest.raises(ApplicationError) as exc_info:
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

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert exc_info.value.non_retryable is True


@pytest.mark.anyio
async def test_resolve_agents_config_classifies_malformed_persisted_agents_as_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = SimpleNamespace(
        id=uuid.uuid4(),
        preset_id=uuid.uuid4(),
        version=1,
        agents={"enabled": True, "subagents": {}},
        tool_approvals={},
    )
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version),
        use_latest_resource_versions=AsyncMock(return_value=False),
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

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_agents_config_activity(
            ResolveAgentsConfigActivityInput(
                role=role,
                agents=AgentSubagentsConfig.model_validate(
                    {
                        "enabled": True,
                        "subagents": [{"preset": "malformed-child"}],
                    }
                ),
            )
        )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.AGENT_PREPARATION_FAILED
    assert exc_info.value.non_retryable is True


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
        lambda **_: _AsyncContext(
            SimpleNamespace(use_latest_resource_versions=AsyncMock(return_value=False))
        ),
    )

    with pytest.raises(ApplicationError) as exc_info:
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

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert exc_info.value.non_retryable is True


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


@pytest.mark.anyio
@pytest.mark.parametrize(
    "credentials",
    [None, {"CUSTOM_MODEL_PROVIDER_API_KEY": "opaque-secret"}],
)
async def test_resolve_custom_model_provider_config_classifies_invalid_config(
    monkeypatch: pytest.MonkeyPatch,
    credentials: dict[str, str] | None,
) -> None:
    service = SimpleNamespace(
        get_workspace_provider_credentials=AsyncMock(return_value=credentials)
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

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_custom_model_provider_config_activity(role)

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert exc_info.value.message == "Agent configuration is invalid"
    assert "opaque-secret" not in str(exc_info.value)


@pytest.mark.anyio
async def test_resolve_custom_model_provider_config_classifies_revoked_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_id = uuid.uuid4()
    service = SimpleNamespace(
        session=SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    scalar_one_or_none=lambda: SimpleNamespace(
                        custom_provider_id=uuid.uuid4()
                    )
                )
            )
        ),
        organization_id=uuid.uuid4(),
        get_catalog_credentials=AsyncMock(
            side_effect=TracecatAuthorizationError("catalog access revoked")
        ),
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

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_custom_model_provider_config_activity(role, catalog_id)

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert exc_info.value.non_retryable is True
    assert "catalog access revoked" not in str(exc_info.value)
